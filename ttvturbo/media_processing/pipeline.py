"""VOD Pipeline orchestration.

Orchestrates the three reusable services:

1. :class:`vod_pipeline.service.VodPipelineService` (download);
2. :class:`media_processing.audio_extraction.AudioExtractionService`;
3. :class:`media_processing.transcription.TranscriptionService`.

The pipeline module does NOT implement download, audio extraction or
transcription logic itself. It only:

* creates a pipeline run record;
* inspects the current state of each step's underlying job/artifact;
* starts the next step when the previous one is READY;
* marks the run READY_FOR_CLIP_ANALYSIS when transcription is READY;
* handles cancel / retry / restart recovery.

A background orchestrator thread advances each RUNNING run. The thread
is daemon and exits when no runs are active.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ttvturbo.vod_pipeline import (
    VodConflictError,
    VodNotFoundError,
    VodPipelineService,
    VodStatus,
)

from .audio_extraction import AudioExtractionService
from .schemas import (
    CANCELLABLE_JOB_STATUSES,
    MediaJobStatus,
    MediaSourceNotFoundError,
    PipelineRunConflictError,
    PipelineRunNotFoundError,
    PipelineRunStorageError,
    PipelineRunValidationError,
    PipelineStatus,
    PipelineStepStatus,
    PipelineStepType,
    RETRYABLE_JOB_STATUSES,
    SCHEMA_VERSION,
    TRANSIENT_JOB_STATUSES,
)
from .storage import MediaJobStorage
from .transcription import TranscriptionService

logger = logging.getLogger("ttvturbo.media_processing.pipeline")

ORCHESTRATOR_POLL_SECONDS = 2.0


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


class PipelineError(Exception):
    """Pipeline-specific error."""


def _initial_steps() -> list[dict]:
    return [
        {"type": PipelineStepType.DOWNLOAD.value, "status": PipelineStepStatus.WAITING.value, "job_id": None, "error": None},
        {"type": PipelineStepType.EXTRACT_AUDIO.value, "status": PipelineStepStatus.WAITING.value, "job_id": None, "error": None},
        {"type": PipelineStepType.TRANSCRIBE.value, "status": PipelineStepStatus.WAITING.value, "job_id": None, "error": None},
        {"type": PipelineStepType.FIND_CLIPS.value, "status": PipelineStepStatus.NOT_IMPLEMENTED.value, "job_id": None, "error": None},
    ]


class PipelineService:
    """Orchestrates download -> audio -> transcription for a VOD."""

    def __init__(
        self,
        storage: MediaJobStorage,
        vod_service: VodPipelineService,
        audio_service: AudioExtractionService,
        transcription_service: TranscriptionService,
    ) -> None:
        self.storage = storage
        self.vod_service = vod_service
        self.audio_service = audio_service
        self.transcription_service = transcription_service
        self._lock = threading.Lock()
        self._orchestrator_thread: Optional[threading.Thread] = None
        self._orchestrator_stop = threading.Event()
        self._recover_on_startup()

    # ------------------------------------------------------------------ public
    def start_run(self, source_type: str, source_id: str) -> dict:
        if source_type != "twitch_vod":
            raise PipelineRunValidationError(f"unsupported source_type {source_type!r}")
        # Verify the VOD exists.
        try:
            vod = self.vod_service.storage.load_vod(source_id)
        except VodNotFoundError as exc:
            raise PipelineRunNotFoundError(f"vod not found: {source_id}") from exc
        profile_id = vod.get("profile_id")

        # Check for an existing active run for the same VOD.
        for run in self.storage.iter_runs():
            if run.get("source_id") == source_id and run.get("status") in {
                PipelineStatus.QUEUED.value,
                PipelineStatus.RUNNING.value,
                PipelineStatus.WAITING_FOR_GPU.value,
            }:
                raise PipelineRunConflictError(
                    "A pipeline run is already active for this VOD."
                )

        run_id = _new_uuid()
        now = _now_iso()
        run = {
            "schema_version": SCHEMA_VERSION,
            "id": run_id,
            "source_type": source_type,
            "source_id": source_id,
            "profile_id": profile_id,
            "status": PipelineStatus.RUNNING.value,
            "steps": _initial_steps(),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        self.storage.save_run(run)
        self._ensure_orchestrator()
        return self.storage.load_run(run_id)

    def get_run(self, run_id: str) -> dict:
        try:
            return self.storage.load_run(run_id)
        except PipelineRunStorageError as exc:
            raise PipelineRunNotFoundError(str(exc)) from exc

    def list_runs(self, source_id: Optional[str] = None) -> list[dict]:
        runs = list(self.storage.iter_runs())
        if source_id:
            runs = [r for r in runs if r.get("source_id") == source_id]
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return runs

    def cancel_run(self, run_id: str) -> dict:
        run = self.get_run(run_id)
        status = run.get("status")
        if status not in {
            PipelineStatus.QUEUED.value,
            PipelineStatus.RUNNING.value,
            PipelineStatus.WAITING_FOR_GPU.value,
        }:
            raise PipelineRunConflictError(
                f"Run can only be canceled while active (current: {status})."
            )
        # Cancel any active underlying job.
        for step in run.get("steps", []):
            job_id = step.get("job_id")
            if not job_id:
                continue
            step_type = step.get("type")
            try:
                if step_type == PipelineStepType.DOWNLOAD.value:
                    self.vod_service.cancel_download(run["source_id"])
                elif step_type == PipelineStepType.EXTRACT_AUDIO.value:
                    self.audio_service.cancel_job(job_id)
                elif step_type == PipelineStepType.TRANSCRIBE.value:
                    self.transcription_service.cancel_job(job_id)
            except Exception as exc:
                logger.warning("cancel of step %s job %s failed: %s", step_type, job_id, exc)
        run = self.storage.load_run(run_id)
        run["status"] = PipelineStatus.CANCELED.value
        run["completed_at"] = _now_iso()
        run["updated_at"] = _now_iso()
        self.storage.save_run(run)
        return run

    def retry_run(self, run_id: str) -> dict:
        run = self.get_run(run_id)
        status = run.get("status")
        if status not in {PipelineStatus.FAILED.value, PipelineStatus.CANCELED.value}:
            raise PipelineRunConflictError(
                "Retry is only allowed for FAILED or CANCELED runs."
            )
        # Reset failed steps to WAITING and clear the run error.
        steps = run.get("steps") or []
        for step in steps:
            if step.get("status") == PipelineStepStatus.FAILED.value:
                step["status"] = PipelineStepStatus.WAITING.value
                step["error"] = None
                # Keep the job_id so the orchestrator can retry it, or
                # clear it so a fresh job is created.
                step["job_id"] = None
        run["steps"] = steps
        run["status"] = PipelineStatus.RUNNING.value
        run["error"] = None
        run["completed_at"] = None
        run["updated_at"] = _now_iso()
        self.storage.save_run(run)
        self._ensure_orchestrator()
        return self.storage.load_run(run_id)

    def delete_run(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        status = run.get("status")
        if status in {
            PipelineStatus.QUEUED.value,
            PipelineStatus.RUNNING.value,
            PipelineStatus.WAITING_FOR_GPU.value,
        }:
            raise PipelineRunConflictError("Cannot delete a running run. Cancel it first.")
        return self.storage.delete_run(run_id)

    def aggregate_status(self) -> dict:
        runs = list(self.storage.iter_runs())
        active = sum(1 for r in runs if r.get("status") in {
            PipelineStatus.QUEUED.value, PipelineStatus.RUNNING.value, PipelineStatus.WAITING_FOR_GPU.value,
        })
        ready = sum(1 for r in runs if r.get("status") == PipelineStatus.READY_FOR_CLIP_ANALYSIS.value)
        failed = sum(1 for r in runs if r.get("status") == PipelineStatus.FAILED.value)
        return {"total": len(runs), "active": active, "ready_for_clip_analysis": ready, "failed": failed}

    # ------------------------------------------------------------------ orchestrator
    def shutdown(self) -> None:
        """Stop the orchestrator thread.

        Idempotent: safe to call multiple times.  Signals the orchestrator
        loop to stop and waits briefly for it to exit.  Does not raise.
        """
        self._orchestrator_stop.set()
        t = self._orchestrator_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        with self._lock:
            self._orchestrator_thread = None

    def _ensure_orchestrator(self) -> None:
        with self._lock:
            if self._orchestrator_thread is not None and self._orchestrator_thread.is_alive():
                return
            self._orchestrator_stop.clear()
            t = threading.Thread(
                target=self._orchestrator_loop, daemon=True, name="pipeline-orchestrator",
            )
            self._orchestrator_thread = t
            t.start()

    def _orchestrator_loop(self) -> None:
        while not self._orchestrator_stop.is_set():
            try:
                self._advance_runs()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("orchestrator iteration failed: %s", exc)
            # Check if any runs are still active.
            active = any(
                r.get("status") in {
                    PipelineStatus.QUEUED.value, PipelineStatus.RUNNING.value, PipelineStatus.WAITING_FOR_GPU.value,
                }
                for r in self.storage.iter_runs()
            )
            if not active:
                self._orchestrator_stop.set()
                break
            time.sleep(ORCHESTRATOR_POLL_SECONDS)

    def _advance_runs(self) -> None:
        for run in list(self.storage.iter_runs()):
            if run.get("status") not in {
                PipelineStatus.RUNNING.value, PipelineStatus.WAITING_FOR_GPU.value,
            }:
                continue
            try:
                self._advance_run(run)
            except Exception as exc:
                logger.warning("advance run %s failed: %s", run.get("id"), exc)

    def _advance_run(self, run: dict) -> None:
        run_id = run["id"]
        source_id = run["source_id"]
        steps = run.get("steps") or []
        # Reload the VOD to get its current status.
        try:
            vod = self.vod_service.storage.load_vod(source_id)
        except VodNotFoundError:
            self._fail_run(run_id, "VOD no longer exists.")
            return
        vod_status = vod.get("status")

        # --- Step 1: DOWNLOAD ---
        dl_step = next((s for s in steps if s.get("type") == PipelineStepType.DOWNLOAD.value), None)
        if dl_step:
            if vod_status == VodStatus.READY.value:
                if dl_step.get("status") != PipelineStepStatus.READY.value:
                    dl_step["status"] = PipelineStepStatus.READY.value
                    dl_step["error"] = None
            elif vod_status in {s.value for s in TRANSIENT_JOB_STATUSES if s != VodStatus.READY} or vod_status in (
                VodStatus.QUEUED.value, VodStatus.DOWNLOADING.value, VodStatus.VERIFYING.value,
            ):
                dl_step["status"] = PipelineStepStatus.RUNNING.value
                if not dl_step.get("job_id"):
                    dl_step["job_id"] = "vod:" + source_id
            elif vod_status in {VodStatus.DISCOVERED.value, VodStatus.FAILED.value, VodStatus.CANCELED.value}:
                if dl_step.get("status") not in {PipelineStepStatus.RUNNING.value, PipelineStepStatus.READY.value}:
                    # Start the download via the existing service.
                    try:
                        self.vod_service.start_download(source_id)
                        dl_step["status"] = PipelineStepStatus.RUNNING.value
                        dl_step["job_id"] = "vod:" + source_id
                        dl_step["error"] = None
                    except VodConflictError as exc:
                        dl_step["status"] = PipelineStepStatus.RUNNING.value
                        dl_step["job_id"] = "vod:" + source_id
                    except Exception as exc:
                        dl_step["status"] = PipelineStepStatus.FAILED.value
                        dl_step["error"] = str(exc)
                        self._fail_run(run_id, f"Download step failed: {exc}")
                        self._save_run(run_id, steps=steps)
                        return

        # --- Step 2: EXTRACT_AUDIO ---
        audio_step = next((s for s in steps if s.get("type") == PipelineStepType.EXTRACT_AUDIO.value), None)
        if audio_step and dl_step and dl_step.get("status") == PipelineStepStatus.READY.value:
            # Check for an existing audio artifact.
            audio_artifact = self.audio_service.get_audio_artifact(source_id)
            if audio_artifact is not None:
                audio_step["status"] = PipelineStepStatus.READY.value
                audio_step["error"] = None
                audio_step["job_id"] = audio_artifact.get("produced_by_job_id")
            else:
                # Check the audio job status if we already started one.
                job_id = audio_step.get("job_id")
                if job_id:
                    try:
                        job = self.audio_service.get_job(job_id)
                        jstatus = job.get("status")
                        if jstatus == MediaJobStatus.READY.value:
                            audio_step["status"] = PipelineStepStatus.READY.value
                            audio_step["error"] = None
                        elif jstatus in {s.value for s in TRANSIENT_JOB_STATUSES}:
                            audio_step["status"] = PipelineStepStatus.RUNNING.value
                        elif jstatus == MediaJobStatus.FAILED.value:
                            audio_step["status"] = PipelineStepStatus.FAILED.value
                            audio_step["error"] = job.get("error") or "audio extraction failed"
                            self._fail_run(run_id, f"Audio step failed: {audio_step['error']}")
                            self._save_run(run_id, steps=steps)
                            return
                        elif jstatus == MediaJobStatus.CANCELED.value:
                            audio_step["status"] = PipelineStepStatus.FAILED.value
                            audio_step["error"] = "audio extraction was canceled"
                            self._fail_run(run_id, "Audio step was canceled.")
                            self._save_run(run_id, steps=steps)
                            return
                    except Exception as exc:
                        logger.warning("could not load audio job %s: %s", job_id, exc)
                if audio_step.get("status") == PipelineStepStatus.WAITING.value:
                    # Start audio extraction.
                    try:
                        job = self.audio_service.start_extraction("twitch_vod", source_id)
                        audio_step["status"] = PipelineStepStatus.RUNNING.value
                        audio_step["job_id"] = job.get("id")
                        audio_step["error"] = None
                    except MediaSourceNotFoundError as exc:
                        self._fail_run(run_id, f"Audio step failed: {exc}")
                        self._save_run(run_id, steps=steps)
                        return
                    except Exception as exc:
                        audio_step["status"] = PipelineStepStatus.FAILED.value
                        audio_step["error"] = str(exc)
                        self._fail_run(run_id, f"Audio step failed: {exc}")
                        self._save_run(run_id, steps=steps)
                        return

        # --- Step 3: TRANSCRIBE ---
        tr_step = next((s for s in steps if s.get("type") == PipelineStepType.TRANSCRIBE.value), None)
        if tr_step and audio_step and audio_step.get("status") == PipelineStepStatus.READY.value:
            # Check for an existing READY transcription for this VOD.
            existing_transcriptions = self.transcription_service.list_transcriptions(source_id)
            ready_transcription = next(
                (t for t in existing_transcriptions if t.get("status") == "READY"), None
            )
            if ready_transcription is not None:
                tr_step["status"] = PipelineStepStatus.READY.value
                tr_step["error"] = None
            else:
                job_id = tr_step.get("job_id")
                if job_id:
                    try:
                        job = self.transcription_service.get_job(job_id)
                        jstatus = job.get("status")
                        if jstatus == MediaJobStatus.READY.value:
                            tr_step["status"] = PipelineStepStatus.READY.value
                            tr_step["error"] = None
                        elif jstatus in {s.value for s in TRANSIENT_JOB_STATUSES}:
                            tr_step["status"] = (
                                PipelineStepStatus.WAITING_FOR_GPU.value
                                if jstatus == MediaJobStatus.WAITING_FOR_GPU.value
                                else PipelineStepStatus.RUNNING.value
                            )
                        elif jstatus == MediaJobStatus.FAILED.value:
                            tr_step["status"] = PipelineStepStatus.FAILED.value
                            tr_step["error"] = job.get("error") or "transcription failed"
                            self._fail_run(run_id, f"Transcription step failed: {tr_step['error']}")
                            self._save_run(run_id, steps=steps)
                            return
                        elif jstatus == MediaJobStatus.CANCELED.value:
                            tr_step["status"] = PipelineStepStatus.FAILED.value
                            tr_step["error"] = "transcription was canceled"
                            self._fail_run(run_id, "Transcription step was canceled.")
                            self._save_run(run_id, steps=steps)
                            return
                    except Exception as exc:
                        logger.warning("could not load transcription job %s: %s", job_id, exc)
                if tr_step.get("status") == PipelineStepStatus.WAITING.value:
                    # Start transcription.
                    try:
                        job = self.transcription_service.start_transcription("twitch_vod", source_id)
                        tr_step["status"] = PipelineStepStatus.RUNNING.value
                        tr_step["job_id"] = job.get("id")
                        tr_step["error"] = None
                    except Exception as exc:
                        tr_step["status"] = PipelineStepStatus.FAILED.value
                        tr_step["error"] = str(exc)
                        self._fail_run(run_id, f"Transcription step failed: {exc}")
                        self._save_run(run_id, steps=steps)
                        return

        # --- Finalize ---
        if (
            dl_step and dl_step.get("status") == PipelineStepStatus.READY.value
            and audio_step and audio_step.get("status") == PipelineStepStatus.READY.value
            and tr_step and tr_step.get("status") == PipelineStepStatus.READY.value
        ):
            run["status"] = PipelineStatus.READY_FOR_CLIP_ANALYSIS.value
            run["completed_at"] = _now_iso()
        elif tr_step and tr_step.get("status") == PipelineStepStatus.WAITING_FOR_GPU.value:
            run["status"] = PipelineStatus.WAITING_FOR_GPU.value
        else:
            run["status"] = PipelineStatus.RUNNING.value
        run["steps"] = steps
        run["updated_at"] = _now_iso()
        self.storage.save_run(run)

    def _save_run(self, run_id: str, steps: list[dict]) -> None:
        run = self.storage.load_run(run_id)
        run["steps"] = steps
        run["updated_at"] = _now_iso()
        self.storage.save_run(run)

    def _fail_run(self, run_id: str, reason: str) -> None:
        run = self.storage.load_run(run_id)
        run["status"] = PipelineStatus.FAILED.value
        run["error"] = reason
        run["completed_at"] = _now_iso()
        run["updated_at"] = _now_iso()
        self.storage.save_run(run)

    def _recover_on_startup(self) -> None:
        """Mark transient runs as appropriate after a restart.

        RUNNING/WAITING_FOR_GPU runs are kept RUNNING so the orchestrator
        re-evaluates their actual step state from the underlying jobs and
        artifacts. This avoids re-running finished steps.
        """
        # Ensure the orchestrator starts if any runs are active.
        active = any(
            r.get("status") in {
                PipelineStatus.QUEUED.value, PipelineStatus.RUNNING.value, PipelineStatus.WAITING_FOR_GPU.value,
            }
            for r in self.storage.iter_runs()
        )
        if active:
            self._ensure_orchestrator()
