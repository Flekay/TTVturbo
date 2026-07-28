"""Reusable audio-extraction service.

Extracts a mono 16 kHz FLAC audio track from a registered, verified
Twitch VOD source file. Runs FFmpeg as a separate subprocess (no
``shell=True``, no shell strings). The result is stored as a VOD
artifact under ``vods/{vod_id}/artifacts/audio/source_audio.flac`` with
a sidecar ``metadata.json`` containing SHA-256, size, duration, sample
rate, channels and codec.

Reuse: if a valid audio artifact already exists, it is returned by
default. An optional ``force`` flag re-extracts.

The service is reused by:

* the on-demand Transcription page (via the transcription service which
  depends on a ready audio artifact);
* the VOD Pipeline orchestration (which calls the same service).

No download logic lives here — the source must already be a READY VOD.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .gpu_lock import GpuLock, GpuLockBusyError, GpuLockOwner, GpuLockError
from .schemas import (
    AudioArtifactMetadata,
    CANCELLABLE_JOB_STATUSES,
    JobType,
    MediaJob,
    MediaJobConflictError,
    MediaJobNotFoundError,
    MediaJobStatus,
    MediaJobStorageError,
    MediaJobValidationError,
    MediaProgress,
    MediaSourceError,
    MediaSourceNotFoundError,
    MediaSourceNotReadyError,
    RETRYABLE_JOB_STATUSES,
    SCHEMA_VERSION,
    TRANSIENT_JOB_STATUSES,
)
from .sources import MediaSourceResolver, ResolvedMediaSource
from .storage import MediaJobStorage

logger = logging.getLogger("ttvturbo.media_processing.audio_extraction")

ARTIFACTS_SUBDIR = "artifacts"
AUDIO_SUBDIR = "audio"
AUDIO_FILENAME = "source_audio.flac"
AUDIO_METADATA_FILENAME = "metadata.json"
AUDIO_PART_SUFFIX = ".part"

KILL_GRACE_SECONDS = 5.0


class AudioExtractionError(Exception):
    """Audio-extraction-specific error."""


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_ffmpeg() -> str:
    """Return the ffmpeg executable path or raise."""
    # Reuse the app-level helper if available, else fall back to PATH.
    try:
        from ttvturbo.app import _find_executable  # type: ignore[import-not-found]

        found = _find_executable("ffmpeg")
    except Exception:
        found = shutil.which("ffmpeg")
    if not found:
        raise AudioExtractionError("ffmpeg is not installed or not on PATH")
    return found


def _find_ffprobe() -> str:
    try:
        from ttvturbo.app import _find_executable  # type: ignore[import-not-found]

        found = _find_executable("ffprobe")
    except Exception:
        found = shutil.which("ffprobe")
    if not found:
        raise AudioExtractionError("ffprobe is not installed or not on PATH")
    return found


def ffprobe_audio_info(path: Path) -> dict:
    """Return audio stream info for a FLAC file via ffprobe."""
    ffprobe = _find_ffprobe()
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise AudioExtractionError(f"ffprobe failed: {stderr}")
    try:
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AudioExtractionError(f"ffprobe returned non-JSON: {exc}") from exc
    streams = payload.get("streams") or []
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio_stream is None:
        raise AudioExtractionError("no audio stream found in FLAC")
    fmt = payload.get("format") or {}
    duration = None
    try:
        duration = float(fmt.get("duration") or audio_stream.get("duration") or 0)
    except (TypeError, ValueError):
        duration = None
    return {
        "codec": audio_stream.get("codec_name") or "flac",
        "sample_rate": int(audio_stream.get("sample_rate") or 16000),
        "channels": int(audio_stream.get("channels") or 1),
        "duration_seconds": duration,
    }


class AudioExtractionService:
    """Service that extracts audio from a READY VOD into a VOD artifact.

    The service owns:

    * a :class:`MediaJobStorage` for EXTRACT_AUDIO jobs;
    * a :class:`MediaSourceResolver` for source verification;
    * a single in-process concurrency slot (one extraction at a time);
    * a subprocess worker (:mod:`media_processing.audio_extraction_worker`)
      so FastAPI stays responsive during multi-hour extractions.

    Audio extraction is CPU/IO bound (FFmpeg), not GPU bound, so it does
    NOT acquire the GPU lock. The transcription service acquires the GPU
    lock later.
    """

    def __init__(
        self,
        storage: MediaJobStorage,
        source_resolver: MediaSourceResolver,
        on_job_ready: Any = None,
    ) -> None:
        self.storage = storage
        self.source_resolver = source_resolver
        self._on_job_ready = on_job_ready
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen] = {}
        self._active_log_fh: dict[str, Any] = {}
        # Recover orphaned jobs on startup.
        self._recover_on_startup()

    # ------------------------------------------------------------------ public
    def artifact_dir(self, vod_id: str, source_type: str = "twitch_vod") -> Path:
        if source_type == "twitch_vod":
            vod_dir = self.source_resolver.get_vod_dir(vod_id)
        else:
            vod_dir = self.source_resolver.get_source_dir(source_type, vod_id)
        return vod_dir / ARTIFACTS_SUBDIR / AUDIO_SUBDIR

    def artifact_dir_for(self, source_type: str, source_id: str) -> Path:
        """Generalized artifact_dir that supports any source type."""
        source_dir = self.source_resolver.get_source_dir(source_type, source_id)
        return source_dir / ARTIFACTS_SUBDIR / AUDIO_SUBDIR

    def artifact_path(self, vod_id: str, source_type: str = "twitch_vod") -> Path:
        return self.artifact_dir(vod_id, source_type) / AUDIO_FILENAME

    def artifact_metadata_path(self, vod_id: str, source_type: str = "twitch_vod") -> Path:
        return self.artifact_dir(vod_id, source_type) / AUDIO_METADATA_FILENAME

    def get_audio_artifact(self, vod_id: str, source_type: str = "twitch_vod") -> Optional[dict]:
        """Return the persisted audio artifact metadata if it exists and
        the FLAC file is present on disk, else None.
        """
        meta_path = self.artifact_metadata_path(vod_id, source_type)
        if not meta_path.is_file():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        flac = self.artifact_path(vod_id, source_type)
        if not flac.is_file():
            return None
        return payload

    def start_extraction(self, source_type: str, source_id: str, force: bool = False) -> dict:
        """Start an EXTRACT_AUDIO job for ``(source_type, source_id)``.

        If a valid audio artifact already exists and ``force`` is False,
        no new job is started; the existing artifact metadata is returned
        directly (wrapped in a job-shaped dict with status READY).
        """
        if source_type not in ("twitch_vod", "file_upload"):
            raise MediaSourceError(f"unsupported source_type {source_type!r}")
        # Verify the source is READY before creating any job.
        resolved = self.source_resolver.resolve(source_type, source_id)

        # Reuse existing artifact unless forced.
        if not force:
            existing = self.get_audio_artifact(source_id, source_type)
            if existing is not None:
                return existing

        # Cancel any existing RUNNING job for the same source first? No —
        # we treat duplicate starts as a conflict to avoid double writes.
        existing_jobs = [
            j for j in self.storage.iter_jobs()
            if j.get("job_type") == JobType.EXTRACT_AUDIO.value
            and j.get("source_type") == source_type
            and j.get("source_id") == source_id
            and j.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES}
        ]
        if existing_jobs:
            raise MediaJobConflictError(
                "An audio extraction is already running for this source."
            )

        job_id = _new_uuid()
        now = _now_iso()
        job = {
            "schema_version": SCHEMA_VERSION,
            "id": job_id,
            "job_type": JobType.EXTRACT_AUDIO.value,
            "source_type": source_type,
            "source_id": source_id,
            "status": MediaJobStatus.QUEUED.value,
            "progress": {"percent": None, "processed_seconds": None, "total_seconds": None, "phase": None},
            "options": {"force": bool(force)},
            "result": None,
            "error": None,
            "depends_on": None,
            "transcription_id": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now,
        }
        self.storage.save_job(job)
        self._spawn_worker(job_id, resolved)
        return self.storage.load_job(job_id)

    def get_job(self, job_id: str) -> dict:
        try:
            return self.storage.load_job(job_id)
        except MediaJobStorageError as exc:
            raise MediaJobNotFoundError(str(exc)) from exc

    def list_jobs(self, source_type: Optional[str] = None, source_id: Optional[str] = None) -> list[dict]:
        jobs = list(self.storage.iter_jobs())
        jobs = [j for j in jobs if j.get("job_type") == JobType.EXTRACT_AUDIO.value]
        if source_type:
            jobs = [j for j in jobs if j.get("source_type") == source_type]
        if source_id:
            jobs = [j for j in jobs if j.get("source_id") == source_id]
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return jobs

    def cancel_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        status = job.get("status")
        if status not in {s.value for s in CANCELLABLE_JOB_STATUSES}:
            raise MediaJobConflictError(
                f"Job can only be canceled while active (current: {status})."
            )
        with self._lock:
            proc = self._active.get(job_id)
        if proc is not None and proc.poll() is None:
            self._terminate(proc)
            try:
                proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._kill(proc)
            with self._lock:
                self._active.pop(job_id, None)
            self._close_log(job_id)
        job = self.storage.load_job(job_id)
        job["status"] = MediaJobStatus.CANCELED.value
        job["error"] = "Audio extraction was canceled by the user."
        job["completed_at"] = _now_iso()
        job["updated_at"] = _now_iso()
        self.storage.save_job(job)
        # Remove the .part file if present.
        source_id = job.get("source_id")
        source_type = job.get("source_type", "twitch_vod")
        if source_id:
            part = self.artifact_path(source_id, source_type).with_name(AUDIO_FILENAME + AUDIO_PART_SUFFIX)
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
        return job

    def retry_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        status = job.get("status")
        if status not in {s.value for s in RETRYABLE_JOB_STATUSES}:
            raise MediaJobConflictError(
                "Retry is only allowed for FAILED or CANCELED jobs."
            )
        source_type = job.get("source_type")
        source_id = job.get("source_id")
        if not source_type or not source_id:
            raise MediaJobValidationError("job is missing source_type/source_id")
        # Re-resolve to ensure the source is still READY.
        resolved = self.source_resolver.resolve(source_type, source_id)
        # Reset the existing job record and re-spawn.
        now = _now_iso()
        job["status"] = MediaJobStatus.QUEUED.value
        job["error"] = None
        job["result"] = None
        job["progress"] = {"percent": None, "processed_seconds": None, "total_seconds": None, "phase": None}
        job["started_at"] = None
        job["completed_at"] = None
        job["updated_at"] = now
        self.storage.save_job(job)
        self._spawn_worker(job_id, resolved)
        return self.storage.load_job(job_id)

    def delete_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        status = job.get("status")
        if status in {s.value for s in TRANSIENT_JOB_STATUSES}:
            raise MediaJobConflictError("Cannot delete a running job. Cancel it first.")
        return self.storage.delete_job(job_id)

    # ------------------------------------------------------------------ worker
    def _spawn_worker(self, job_id: str, resolved: ResolvedMediaSource) -> None:
        job_dir = self.storage._job_dir(job_id)  # noqa: SLF001
        job_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = self.artifact_dir(resolved.source_id, resolved.source_type)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        worker_job = {
            "job_id": job_id,
            "job_path": str(self.storage.job_path(job_id)),
            "source_type": resolved.source_type,
            "source_id": resolved.source_id,
            "source_file": str(resolved.file_path),
            "artifact_dir": str(artifact_dir),
            "audio_filename": AUDIO_FILENAME,
            "audio_metadata_filename": AUDIO_METADATA_FILENAME,
        }
        worker_job_path = job_dir / "worker_job.json"
        with open(worker_job_path, "w", encoding="utf-8") as fh:
            json.dump(worker_job, fh, indent=2, ensure_ascii=False)
        log_path = self.storage.worker_log_path(job_id)
        try:
            log_fh = open(log_path, "wb", buffering=0)
        except OSError as exc:
            self._mark_failed(job_id, f"Could not open worker log file: {exc}")
            raise MediaJobConflictError(f"Could not open worker log file: {exc}") from exc
        cmd = [sys.executable, "-m", "ttvturbo.media_processing.audio_extraction_worker", str(worker_job_path)]
        try:
            proc = subprocess.Popen(
                cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            try:
                log_fh.close()
            except OSError:
                pass
            self._mark_failed(job_id, f"Could not start worker subprocess: {exc}")
            raise MediaJobConflictError(f"Could not start worker subprocess: {exc}") from exc
        with self._lock:
            self._active[job_id] = proc
            self._active_log_fh[job_id] = log_fh
        reaper = threading.Thread(
            target=self._reap_worker, args=(job_id, proc, log_fh),
            daemon=True, name=f"audio-reaper-{job_id}",
        )
        reaper.start()

    def _reap_worker(self, job_id: str, proc: subprocess.Popen, log_fh: Any) -> None:
        try:
            exit_code = proc.wait()
        except Exception:  # pragma: no cover - defensive
            exit_code = -1
        try:
            log_fh.close()
        except OSError:
            pass
        # If the worker exited while the job is still transient, mark FAILED.
        became_ready = False
        try:
            job = self.storage.load_job(job_id)
        except MediaJobStorageError:
            job = None
        if job is not None:
            status = job.get("status")
            if status in {s.value for s in TRANSIENT_JOB_STATUSES}:
                self._mark_failed(
                    job_id,
                    f"Audio extraction worker exited with code {exit_code} before completion.",
                )
            elif status == MediaJobStatus.READY.value:
                became_ready = True
        with self._lock:
            if self._active.get(job_id) is proc:
                self._active.pop(job_id, None)
                self._active_log_fh.pop(job_id, None)
        # Notify the callback (e.g. transcription service) that the audio
        # job became READY so dependent TRANSCRIBE jobs can be started.
        if became_ready and self._on_job_ready is not None:
            try:
                self._on_job_ready(job_id)
            except Exception:  # pragma: no cover - defensive
                logger.exception("on_job_ready callback failed for job %s", job_id)

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass

    def _close_log(self, job_id: str) -> None:
        fh = self._active_log_fh.pop(job_id, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def _mark_failed(self, job_id: str, reason: str) -> None:
        try:
            job = self.storage.load_job(job_id)
        except MediaJobStorageError:
            return
        job["status"] = MediaJobStatus.FAILED.value
        job["error"] = reason
        job["completed_at"] = _now_iso()
        job["updated_at"] = _now_iso()
        try:
            self.storage.save_job(job)
        except OSError:  # pragma: no cover
            pass

    def _recover_on_startup(self) -> None:
        """Mark transient jobs as FAILED (their subprocess is gone)."""
        for job in self.storage.iter_jobs():
            if job.get("job_type") != JobType.EXTRACT_AUDIO.value:
                continue
            if job.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES}:
                job["status"] = MediaJobStatus.FAILED.value
                job["error"] = "Audio extraction was interrupted by a server restart."
                job["completed_at"] = _now_iso()
                job["updated_at"] = _now_iso()
                try:
                    self.storage.save_job(job)
                except OSError:  # pragma: no cover
                    pass
                # Clean up any .part files for this source.
                source_id = job.get("source_id")
                source_type = job.get("source_type", "twitch_vod")
                if source_id:
                    part = self.artifact_path(source_id, source_type).with_name(AUDIO_FILENAME + AUDIO_PART_SUFFIX)
                    try:
                        if part.exists():
                            part.unlink()
                    except OSError:
                        pass

    # ------------------------------------------------------------------ status
    def active_count(self) -> int:
        with self._lock:
            # Reap dead.
            dead = [jid for jid, p in self._active.items() if p.poll() is not None]
            for jid in dead:
                self._active.pop(jid, None)
                self._active_log_fh.pop(jid, None)
            return len(self._active)

    def aggregate_status(self) -> dict:
        jobs = [j for j in self.storage.iter_jobs() if j.get("job_type") == JobType.EXTRACT_AUDIO.value]
        ready = sum(1 for j in jobs if j.get("status") == MediaJobStatus.READY.value)
        failed = sum(1 for j in jobs if j.get("status") == MediaJobStatus.FAILED.value)
        active = sum(1 for j in jobs if j.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES})
        return {"total": len(jobs), "ready": ready, "failed": failed, "active": active}
