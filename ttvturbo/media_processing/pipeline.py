"""VOD Pipeline orchestration.

Orchestrates the reusable services:

1. :class:`vod_pipeline.service.VodPipelineService` (Twitch URL import,
   metadata resolution, profile auto-link, download);
2. :class:`media_processing.audio_extraction.AudioExtractionService`;
3. :class:`media_processing.transcription.TranscriptionService`.

The pipeline module does NOT implement download, audio extraction or
transcription logic itself. It only:

* creates a pipeline run record (URL-based or legacy library-id based);
* resolves Twitch metadata and reuses/creates a Twitch profile;
* reuses an existing library item / VOD when the source is already known
  (dedup by stable Twitch video id, never by URL string);
* inspects the current state of each step's underlying job/artifact;
* starts the next step only after the previous one is READY/SKIPPED;
* tracks monotonic overall progress via fixed per-step weights;
* handles cancel / retry / restart recovery;
* marks the run COMPLETED when transcription is READY.

A background orchestrator thread advances each active run. The thread
is daemon and exits when no runs are active.

Backward compatibility: legacy runs (schema v1, no ``source`` block, old
``DOWNLOAD/EXTRACT_AUDIO/TRANSCRIBE/FIND_CLIPS`` steps) remain readable.
They are normalized on read: missing additive fields default to ``None``
and the run is flagged ``"legacy": True`` in the source block so the UI
can render it as a "Legacy Pipeline Run".
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
import time
import uuid
from typing import Any, Optional

from ttvturbo.vod_pipeline import (
    TwitchClientError,
    TwitchNotFoundError,
    VodConflictError,
    VodNotFoundError,
    VodPipelineService,
    VodStatus,
    VodValidationError,
)
from ttvturbo.vod_pipeline.service import parse_twitch_video_url

from .audio_extraction import AudioExtractionService
from .conversation_mining import (
    ConversationMiningConflictError,
    ConversationMiningNotFoundError,
    ConversationMiningService,
    ConversationMiningUnavailableError,
    ConversationMiningValidationError,
    MiningRunStatus,
)
from .schemas import (
    ACTIVE_PIPELINE_STATUSES,
    CANCELLABLE_PIPELINE_STATUSES,
    CANCELLABLE_JOB_STATUSES,
    DONE_STEP_STATUSES,
    PIPELINE_STEP_WEIGHTS,
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
    TERMINAL_PIPELINE_STATUSES,
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


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------


# The concrete, ordered pipeline. Future real steps (ConversationMiningStep,
# ClipCandidatesStep, ...) are appended here once a real service exists.
# No dynamic imports, no user-supplied step classes, no visual builder.
DEFAULT_VOD_PIPELINE: tuple[str, ...] = (
    PipelineStepType.RESOLVE_SOURCE.value,
    PipelineStepType.DOWNLOAD.value,
    PipelineStepType.EXTRACT_AUDIO.value,
    PipelineStepType.TRANSCRIBE.value,
    PipelineStepType.CONVERSATION_MINING.value,
)


def _new_step(step_type: str) -> dict:
    return {
        "type": step_type,
        "status": PipelineStepStatus.PENDING.value,
        "job_id": None,
        "error": None,
        "progress": None,
        "message": None,
        "attempt": 0,
        "started_at": None,
        "completed_at": None,
        "artifact_ids": [],
    }


def _initial_steps() -> list[dict]:
    return [_new_step(t) for t in DEFAULT_VOD_PIPELINE]


# Legacy runs (pre-URL pipeline) used these four steps. Kept for compat.
_LEGACY_STEP_TYPES = (
    PipelineStepType.DOWNLOAD.value,
    PipelineStepType.EXTRACT_AUDIO.value,
    PipelineStepType.TRANSCRIBE.value,
    PipelineStepType.FIND_CLIPS.value,
)


def _is_legacy_run(run: dict) -> bool:
    """A run is legacy if it has no ``source`` block."""
    return not run.get("source")


def _normalize_run_on_read(run: dict) -> dict:
    """Normalize an old/partial run dict in place so callers always see
    the additive v2 fields.

    Old runs keep their original steps; we only backfill missing fields
    on each step and mark the run as legacy via ``source.legacy = True``.
    """
    if run.get("source") is None:
        run["source"] = {
            "provider": "twitch",
            "type": "vod",
            "external_id": None,
            "url": None,
            "profile_id": run.get("profile_id"),
            "title": None,
            "thumbnail_url": None,
            "duration_seconds": None,
            "legacy": True,
        }
    # Backfill step additive fields.
    for step in run.get("steps", []) or []:
        step.setdefault("progress", None)
        step.setdefault("message", None)
        step.setdefault("attempt", 0)
        step.setdefault("started_at", None)
        step.setdefault("completed_at", None)
        step.setdefault("artifact_ids", [])
    run.setdefault("progress", None)
    run.setdefault("current_step", None)
    run.setdefault("started_at", None)
    run.setdefault("library_item_id", None)
    run.setdefault("transcript_id", None)
    return run


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def _step_weight(step_type: str) -> float:
    return PIPELINE_STEP_WEIGHTS.get(step_type, 0.0)


def _compute_run_progress(run: dict) -> float:
    """Compute a monotonic, weighted overall progress in [0, 100].

    Each step contributes its weight scaled by its own progress:
    * READY/SKIPPED/NOT_IMPLEMENTED -> full weight;
    * RUNNING/WAITING_FOR_GPU -> weight * (step.progress or 0) / 100;
    * PENDING/QUEUED/WAITING/FAILED/CANCELED -> 0.

    The returned value is the max of the computed value and the
    previously stored progress so progress never goes backwards.
    """
    steps = run.get("steps") or []
    total = 0.0
    for step in steps:
        stype = step.get("type")
        status = step.get("status")
        weight = _step_weight(stype)
        if weight <= 0:
            continue
        if status in DONE_STEP_STATUSES:
            total += weight
        elif status in (
            PipelineStepStatus.RUNNING.value,
            PipelineStepStatus.WAITING_FOR_GPU.value,
            PipelineStepStatus.QUEUED.value,
        ):
            sp = step.get("progress")
            if isinstance(sp, (int, float)) and sp > 0:
                total += weight * min(100.0, float(sp)) / 100.0
    total = max(0.0, min(100.0, total))
    prev = run.get("progress")
    if isinstance(prev, (int, float)) and prev > total:
        return float(prev)
    return total


def _current_step(run: dict) -> Optional[str]:
    """Return the first non-done step type, or None if all done."""
    for step in run.get("steps") or []:
        if step.get("status") not in DONE_STEP_STATUSES:
            return step.get("type")
    return None


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _source_block_from_vod(vod: dict, profile_id: Optional[str] = None) -> dict:
    """Build a ``source`` block from a VOD record."""
    vtype = vod.get("type") or "archive"
    return {
        "provider": "twitch",
        "type": "clip" if vtype == "clip" else "vod",
        "external_id": vod.get("twitch_video_id"),
        "url": vod.get("source_url"),
        "profile_id": profile_id or vod.get("profile_id"),
        "title": vod.get("title") or None,
        "thumbnail_url": vod.get("thumbnail_url") or None,
        "duration_seconds": vod.get("duration_seconds"),
        "legacy": False,
    }


class PipelineService:
    """Orchestrates resolve -> download -> audio -> transcription for a URL."""

    def __init__(
        self,
        storage: MediaJobStorage,
        vod_service: VodPipelineService,
        audio_service: AudioExtractionService,
        transcription_service: TranscriptionService,
        mining_service: Optional[ConversationMiningService] = None,
    ) -> None:
        self.storage = storage
        self.vod_service = vod_service
        self.audio_service = audio_service
        self.transcription_service = transcription_service
        self.mining_service = mining_service
        self._lock = threading.Lock()
        self._orchestrator_thread: Optional[threading.Thread] = None
        self._orchestrator_stop = threading.Event()
        self._recover_on_startup()

    # ------------------------------------------------------------------ public
    def start_run_from_url(self, url: str) -> dict:
        """Start a new pipeline run from a Twitch VOD or clip URL.

        Steps performed synchronously here (the RESOLVE_SOURCE step):
        1. normalize + validate the URL (raises on unsupported provider);
        2. fetch Twitch metadata via the existing lister (yt-dlp);
        3. resolve / create a Twitch profile from the broadcaster login;
        4. reuse an existing VOD record by twitch_video_id (dedup) or
           import a new one (no profile required from the caller);
        5. reject if an active run already targets the same external id;
        6. create the run record with the source block and enqueue the
           remaining steps.

        The download itself is started by the orchestrator, not here.
        """
        if not isinstance(url, str) or not url.strip():
            raise PipelineRunValidationError("url must be a non-empty string")
        url = url.strip()
        # Validate URL shape (raises VodValidationError for non-Twitch URLs).
        try:
            external_id, vod_type = parse_twitch_video_url(url)
        except VodValidationError as exc:
            raise PipelineRunValidationError(str(exc)) from exc

        # Fetch metadata via the existing lister. This raises TwitchNotFoundError
        # / TwitchClientError on real failures (no fake data is produced).
        try:
            info = self.vod_service.lister.get_video_info(url)
        except TwitchNotFoundError as exc:
            raise PipelineRunValidationError(f"Twitch video not found: {external_id}") from exc
        except TwitchClientError as exc:
            raise PipelineRunValidationError(f"Could not fetch video metadata: {exc}") from exc
        uploader = (info.get("uploader") or info.get("channel") or "").strip()

        # Resolve / create a Twitch profile from the uploader login (best-effort,
        # not a hard requirement). The VOD import below accepts profile_id=None.
        profile_id: Optional[str] = None
        if uploader:
            profile_id = self._resolve_profile_id(uploader)

        # Reuse an existing VOD record by twitch_video_id (stable dedup).
        existing_vod = self.vod_service.storage.find_vod_by_twitch_video_id(external_id)
        if existing_vod is not None:
            # Re-attach profile if the existing VOD is detached.
            if existing_vod.get("profile_id") is None and profile_id:
                existing_vod["profile_id"] = profile_id
                existing_vod["updated_at"] = _now_iso()
                self.vod_service.storage.save_vod(existing_vod)
            vod = existing_vod
        else:
            # Import a fresh VOD record (no profile required from the caller).
            try:
                vod = self.vod_service.import_vod(url, profile_id=profile_id)
            except VodConflictError as exc:
                # Race: another request imported it. Reload by external id.
                vod = self.vod_service.storage.find_vod_by_twitch_video_id(external_id)
                if vod is None:
                    raise PipelineRunConflictError(str(exc)) from exc

        vod_id = vod["id"]
        source = _source_block_from_vod(vod, profile_id)

        # Reject if an active run already targets the same external id.
        for run in self.storage.iter_runs():
            rs = run.get("source") or {}
            if (
                rs.get("external_id") == external_id
                and run.get("status") in ACTIVE_PIPELINE_STATUSES
            ):
                raise PipelineRunConflictError(
                    "A pipeline run is already active for this source."
                )

        run_id = _new_uuid()
        now = _now_iso()
        steps = _initial_steps()
        # RESOLVE_SOURCE is completed synchronously.
        steps[0]["status"] = PipelineStepStatus.READY.value
        steps[0]["started_at"] = now
        steps[0]["completed_at"] = now
        # If the VOD is already READY (downloaded before), mark DOWNLOAD SKIPPED
        # and record the library item reference.
        library_item_id = vod.get("library_item_id")
        if vod.get("status") == VodStatus.READY.value:
            steps[1]["status"] = PipelineStepStatus.SKIPPED.value
            steps[1]["started_at"] = now
            steps[1]["completed_at"] = now
            if library_item_id:
                steps[1]["artifact_ids"] = [library_item_id]
        run = {
            "schema_version": SCHEMA_VERSION,
            "id": run_id,
            "source_type": "twitch_vod",
            "source_id": vod_id,
            "profile_id": profile_id,
            "status": PipelineStatus.RUNNING.value,
            "steps": steps,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "source": source,
            "progress": _compute_run_progress({"steps": steps, "progress": None}),
            "current_step": _current_step({"steps": steps}),
            "started_at": now,
            "library_item_id": library_item_id,
            "transcript_id": None,
        }
        self.storage.save_run(run)
        self._ensure_orchestrator()
        return self.storage.load_run(run_id)

    def start_run_from_source(self, source: dict) -> dict:
        """Start a run from a unified source contract block.

        The contract mirrors what the URL entry point produces internally::

            {
              "provider": "twitch",
              "source_type": "vod" | "clip",
              "external_id": "<twitch video id>",
              "url": "<optional twitch.tv url>",
            }

        If a VOD with the given ``external_id`` is already imported, it is
        reused (no yt-dlp metadata fetch). Otherwise the URL is used to
        import + start via :meth:`start_run_from_url`. This is the shared
        entry point for both the direct-URL import and the VOD-selection
        start flows so they produce identical runs.
        """
        if not isinstance(source, dict):
            raise PipelineRunValidationError("source must be an object")
        provider = str(source.get("provider") or "").strip().lower()
        if provider != "twitch":
            raise PipelineRunValidationError(f"unsupported provider {provider!r}")
        source_type = str(
            source.get("source_type") or source.get("type") or ""
        ).strip().lower()
        if source_type not in ("vod", "clip"):
            raise PipelineRunValidationError(
                f"unsupported source_type {source_type!r}"
            )
        external_id = str(source.get("external_id") or "").strip()
        if not external_id:
            raise PipelineRunValidationError("external_id is required")

        existing = self.vod_service.storage.find_vod_by_twitch_video_id(external_id)
        if existing is not None:
            return self.start_run("twitch_vod", existing["id"])
        url = str(source.get("url") or "").strip()
        if not url:
            raise PipelineRunValidationError(
                "url is required when the VOD is not yet imported"
            )
        return self.start_run_from_url(url)

    def start_runs_batch(self, sources: list[dict]) -> dict:
        """Start runs for a batch of source contracts.

        Each source is validated and started independently. Partial success
        is allowed: per-source failures are reported in ``failed``, active
        runs (and duplicates within the same request) in ``conflicts``.

        Returns ``{"created": [...], "conflicts": [...], "failed": [...]}``.
        """
        max_batch = 25
        if not isinstance(sources, list):
            raise PipelineRunValidationError("sources must be a list")
        if len(sources) > max_batch:
            raise PipelineRunValidationError(
                f"batch size exceeds maximum of {max_batch}"
            )
        created: list[dict] = []
        conflicts: list[dict] = []
        failed: list[dict] = []
        seen_external_ids: set[str] = set()
        for source in sources:
            external_id = str(source.get("external_id") or "").strip()
            if external_id and external_id in seen_external_ids:
                conflicts.append({
                    "source_external_id": external_id,
                    "code": "duplicate_in_batch",
                    "message": "Duplicate source in batch request.",
                })
                continue
            if external_id:
                seen_external_ids.add(external_id)
            try:
                run = self.start_run_from_source(source)
                created.append({
                    "source_external_id": external_id
                    or (run.get("source") or {}).get("external_id"),
                    "run_id": run["id"],
                })
            except PipelineRunConflictError as exc:
                conflicts.append({
                    "source_external_id": external_id,
                    "code": "active_run",
                    "message": str(exc),
                })
            except Exception as exc:  # noqa: BLE001 - reported per-source
                failed.append({
                    "source_external_id": external_id,
                    "code": "start_failed",
                    "message": str(exc),
                })
        return {"created": created, "conflicts": conflicts, "failed": failed}

    def start_run(self, source_type: str, source_id: str) -> dict:
        """Legacy entry point: start a run for an already-imported VOD id.

        Kept for backward compatibility (library reprocessing). The new
        URL-based entry point is :meth:`start_run_from_url`.
        """
        if source_type != "twitch_vod":
            raise PipelineRunValidationError(f"unsupported source_type {source_type!r}")
        try:
            vod = self.vod_service.storage.load_vod(source_id)
        except VodNotFoundError as exc:
            raise PipelineRunNotFoundError(f"vod not found: {source_id}") from exc
        profile_id = vod.get("profile_id")
        external_id = vod.get("twitch_video_id")

        # Reject if an active run already targets the same source.
        for run in self.storage.iter_runs():
            rs = run.get("source") or {}
            same = (
                (rs.get("external_id") is not None and rs.get("external_id") == external_id)
                or run.get("source_id") == source_id
            )
            if same and run.get("status") in ACTIVE_PIPELINE_STATUSES:
                raise PipelineRunConflictError(
                    "A pipeline run is already active for this VOD."
                )

        run_id = _new_uuid()
        now = _now_iso()
        steps = _initial_steps()
        steps[0]["status"] = PipelineStepStatus.READY.value
        steps[0]["started_at"] = now
        steps[0]["completed_at"] = now
        library_item_id = vod.get("library_item_id")
        if vod.get("status") == VodStatus.READY.value:
            steps[1]["status"] = PipelineStepStatus.SKIPPED.value
            steps[1]["started_at"] = now
            steps[1]["completed_at"] = now
            if library_item_id:
                steps[1]["artifact_ids"] = [library_item_id]
        run = {
            "schema_version": SCHEMA_VERSION,
            "id": run_id,
            "source_type": source_type,
            "source_id": source_id,
            "profile_id": profile_id,
            "status": PipelineStatus.RUNNING.value,
            "steps": steps,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "source": _source_block_from_vod(vod, profile_id),
            "progress": _compute_run_progress({"steps": steps, "progress": None}),
            "current_step": _current_step({"steps": steps}),
            "started_at": now,
            "library_item_id": library_item_id,
            "transcript_id": None,
        }
        self.storage.save_run(run)
        self._ensure_orchestrator()
        return self.storage.load_run(run_id)

    def get_run(self, run_id: str) -> dict:
        try:
            run = self.storage.load_run(run_id)
        except PipelineRunStorageError as exc:
            raise PipelineRunNotFoundError(str(exc)) from exc
        return _normalize_run_on_read(run)

    def list_runs(
        self,
        source_id: Optional[str] = None,
        status: Optional[str] = None,
        profile_id: Optional[str] = None,
        source_type: Optional[str] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        runs = list(self.storage.iter_runs())
        if source_id:
            runs = [r for r in runs if r.get("source_id") == source_id]
        if status:
            # Accept comma-separated lists (e.g. "RUNNING,QUEUED").
            wanted = {s.strip() for s in status.split(",") if s.strip()}
            runs = [r for r in runs if r.get("status") in wanted]
        if profile_id:
            runs = [r for r in runs if r.get("profile_id") == profile_id]
        if source_type:
            runs = [r for r in runs if r.get("source_type") == source_type]
        if search:
            needle = search.strip().lower()
            if needle:
                def _matches(r: dict) -> bool:
                    src = r.get("source") or {}
                    return (
                        needle in (src.get("title") or "").lower()
                        or needle in (src.get("external_id") or "").lower()
                        or needle in (r.get("source_id") or "").lower()
                    )
                runs = [r for r in runs if _matches(r)]
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        if limit is not None and limit > 0:
            runs = runs[:limit]
        return [_normalize_run_on_read(r) for r in runs]

    def cancel_run(self, run_id: str) -> dict:
        run = self.get_run(run_id)
        status = run.get("status")
        if status not in CANCELLABLE_PIPELINE_STATUSES:
            raise PipelineRunConflictError(
                f"Run can only be canceled while active (current: {status})."
            )
        # Mark CANCELING first so the UI sees the intent immediately.
        run["status"] = PipelineStatus.CANCELING.value
        run["updated_at"] = _now_iso()
        self.storage.save_run(run)
        # Cancel any active underlying job (download / audio / transcription / mining).
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
                elif step_type == PipelineStepType.CONVERSATION_MINING.value:
                    if self.mining_service is not None:
                        self.mining_service.cancel_run(job_id)
            except Exception as exc:
                logger.warning("cancel of step %s job %s failed: %s", step_type, job_id, exc)
        # Reload and finalize as CANCELED. Already-finished library assets
        # are preserved (we never delete library items here).
        run = self.storage.load_run(run_id)
        run["status"] = PipelineStatus.CANCELED.value
        run["completed_at"] = _now_iso()
        run["updated_at"] = _now_iso()
        # Mark any still-active step as CANCELED.
        for step in run.get("steps", []):
            if step.get("status") not in DONE_STEP_STATUSES and step.get("status") != PipelineStepStatus.CANCELED.value:
                step["status"] = PipelineStepStatus.CANCELED.value
        run["progress"] = _compute_run_progress(run)
        self.storage.save_run(run)
        return _normalize_run_on_read(run)

    def retry_run(self, run_id: str) -> dict:
        run = self.get_run(run_id)
        status = run.get("status")
        if status in ACTIVE_PIPELINE_STATUSES:
            raise PipelineRunConflictError(
                "Retry is only allowed for terminal runs (FAILED or CANCELED)."
            )
        if status not in {PipelineStatus.FAILED.value, PipelineStatus.CANCELED.value}:
            raise PipelineRunConflictError(
                "Retry is only allowed for FAILED or CANCELED runs."
            )
        # Reset failed / canceled / not-yet-run steps to PENDING and bump the
        # attempt counter on the first failed step. Already-READY/SKIPPED
        # steps keep their artifacts so we resume from the first failure.
        steps = run.get("steps") or []
        now = _now_iso()
        for step in steps:
            st = step.get("status")
            if st == PipelineStepStatus.FAILED.value:
                step["attempt"] = int(step.get("attempt") or 0) + 1
                step["status"] = PipelineStepStatus.PENDING.value
                step["error"] = None
                step["job_id"] = None
                step["progress"] = None
                step["message"] = None
                step["started_at"] = None
                step["completed_at"] = None
            elif st == PipelineStepStatus.CANCELED.value:
                step["status"] = PipelineStepStatus.PENDING.value
                step["job_id"] = None
                step["progress"] = None
                step["message"] = None
                step["started_at"] = None
                step["completed_at"] = None
        run["steps"] = steps
        run["status"] = PipelineStatus.RETRYING.value
        run["error"] = None
        run["completed_at"] = None
        run["updated_at"] = now
        run["progress"] = _compute_run_progress(run)
        run["current_step"] = _current_step(run)
        self.storage.save_run(run)
        # Flip to RUNNING so the orchestrator picks it up.
        run["status"] = PipelineStatus.RUNNING.value
        self.storage.save_run(run)
        self._ensure_orchestrator()
        return self.get_run(run_id)

    def delete_run(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        status = run.get("status")
        if status in ACTIVE_PIPELINE_STATUSES:
            raise PipelineRunConflictError("Cannot delete an active run. Cancel it first.")
        # Deleting a run history entry never touches library items, audio
        # artifacts or transcriptions — those are owned by the library /
        # VOD services.
        return self.storage.delete_run(run_id)

    def aggregate_status(self) -> dict:
        runs = list(self.storage.iter_runs())
        active = sum(1 for r in runs if r.get("status") in ACTIVE_PIPELINE_STATUSES)
        completed = sum(1 for r in runs if r.get("status") == PipelineStatus.COMPLETED.value)
        ready = sum(1 for r in runs if r.get("status") == PipelineStatus.READY_FOR_CLIP_ANALYSIS.value)
        failed = sum(1 for r in runs if r.get("status") == PipelineStatus.FAILED.value)
        canceled = sum(1 for r in runs if r.get("status") == PipelineStatus.CANCELED.value)
        return {
            "total": len(runs),
            "active": active,
            "completed": completed,
            "ready_for_clip_analysis": ready,
            "failed": failed,
            "canceled": canceled,
        }

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
                r.get("status") in ACTIVE_PIPELINE_STATUSES
                for r in self.storage.iter_runs()
            )
            if not active:
                self._orchestrator_stop.set()
                break
            time.sleep(ORCHESTRATOR_POLL_SECONDS)

    def _advance_runs(self) -> None:
        for run in list(self.storage.iter_runs()):
            if run.get("status") not in {
                PipelineStatus.RUNNING.value,
                PipelineStatus.WAITING_FOR_GPU.value,
                PipelineStatus.RETRYING.value,
            }:
                continue
            try:
                self._advance_run(run)
            except Exception as exc:
                logger.warning("advance run %s failed: %s", run.get("id"), exc)

    # ------------------------------------------------------------------ step advancement
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
        library_item_id = vod.get("library_item_id")
        now = _now_iso()

        # Map legacy step types to the new model on the fly so old runs
        # still advance. Legacy runs have no RESOLVE_SOURCE step; we treat
        # resolution as already done.
        step_by_type = {s.get("type"): s for s in steps}

        # --- Step: DOWNLOAD ---
        dl_step = step_by_type.get(PipelineStepType.DOWNLOAD.value)
        if dl_step:
            if vod_status == VodStatus.READY.value:
                if dl_step.get("status") not in DONE_STEP_STATUSES:
                    dl_step["status"] = PipelineStepStatus.READY.value
                    dl_step["error"] = None
                    dl_step["completed_at"] = now
                    if library_item_id and library_item_id not in (dl_step.get("artifact_ids") or []):
                        dl_step["artifact_ids"] = list(dl_step.get("artifact_ids") or []) + [library_item_id]
            elif vod_status in {
                VodStatus.QUEUED.value,
                VodStatus.DOWNLOADING.value,
                VodStatus.VERIFYING.value,
            }:
                dl_step["status"] = PipelineStepStatus.RUNNING.value
                if not dl_step.get("job_id"):
                    dl_step["job_id"] = "vod:" + source_id
                if not dl_step.get("started_at"):
                    dl_step["started_at"] = now
                # Real download progress from the VOD record.
                prog = vod.get("progress") or {}
                pct = prog.get("percent")
                if isinstance(pct, (int, float)):
                    dl_step["progress"] = float(pct)
                else:
                    dl_step["progress"] = None
                dl_bytes = prog.get("downloaded_bytes")
                total_bytes = prog.get("total_bytes")
                speed = prog.get("speed_bytes_per_second")
                eta = prog.get("eta_seconds")
                msg_parts = []
                if dl_bytes is not None and total_bytes is not None:
                    msg_parts.append(f"{dl_bytes} / {total_bytes} bytes")
                if speed is not None:
                    msg_parts.append(f"{speed:.0f} B/s")
                if eta is not None:
                    msg_parts.append(f"ETA {eta:.0f}s")
                dl_step["message"] = " · ".join(msg_parts) or None
            elif vod_status in {VodStatus.DISCOVERED.value, VodStatus.FAILED.value, VodStatus.CANCELED.value}:
                if dl_step.get("status") not in {PipelineStepStatus.RUNNING.value, PipelineStepStatus.READY.value, PipelineStepStatus.SKIPPED.value}:
                    try:
                        self.vod_service.start_download(source_id)
                        dl_step["status"] = PipelineStepStatus.RUNNING.value
                        dl_step["job_id"] = "vod:" + source_id
                        dl_step["error"] = None
                        if not dl_step.get("started_at"):
                            dl_step["started_at"] = now
                    except VodConflictError:
                        dl_step["status"] = PipelineStepStatus.RUNNING.value
                        dl_step["job_id"] = "vod:" + source_id
                    except Exception as exc:
                        dl_step["status"] = PipelineStepStatus.FAILED.value
                        dl_step["error"] = str(exc)
                        self._fail_run(run_id, f"Download step failed: {exc}")
                        self._save_run(run_id, steps=steps)
                        return

        # --- Step: EXTRACT_AUDIO ---
        audio_step = step_by_type.get(PipelineStepType.EXTRACT_AUDIO.value)
        if audio_step and dl_step and dl_step.get("status") in DONE_STEP_STATUSES:
            audio_artifact = self.audio_service.get_audio_artifact(source_id)
            if audio_artifact is not None:
                if audio_step.get("status") not in DONE_STEP_STATUSES:
                    audio_step["status"] = PipelineStepStatus.READY.value
                    audio_step["error"] = None
                    audio_step["completed_at"] = now
                    audio_step["job_id"] = audio_artifact.get("produced_by_job_id")
            else:
                job_id = audio_step.get("job_id")
                if job_id:
                    try:
                        job = self.audio_service.get_job(job_id)
                        jstatus = job.get("status")
                        if jstatus == MediaJobStatus.READY.value:
                            audio_step["status"] = PipelineStepStatus.READY.value
                            audio_step["error"] = None
                            audio_step["completed_at"] = now
                        elif jstatus in {s.value for s in TRANSIENT_JOB_STATUSES}:
                            audio_step["status"] = (
                                PipelineStepStatus.WAITING_FOR_GPU.value
                                if jstatus == MediaJobStatus.WAITING_FOR_GPU.value
                                else PipelineStepStatus.RUNNING.value
                            )
                            prog = job.get("progress") or {}
                            pct = prog.get("percent")
                            audio_step["progress"] = float(pct) if isinstance(pct, (int, float)) else None
                            phase = prog.get("phase")
                            audio_step["message"] = phase or None
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
                if audio_step.get("status") in (PipelineStepStatus.PENDING.value, PipelineStepStatus.WAITING.value):
                    try:
                        job = self.audio_service.start_extraction("twitch_vod", source_id)
                        audio_step["status"] = PipelineStepStatus.RUNNING.value
                        audio_step["job_id"] = job.get("id")
                        audio_step["error"] = None
                        if not audio_step.get("started_at"):
                            audio_step["started_at"] = now
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

        # --- Step: TRANSCRIBE ---
        tr_step = step_by_type.get(PipelineStepType.TRANSCRIBE.value)
        transcript_id: Optional[str] = None
        if tr_step and audio_step and audio_step.get("status") in DONE_STEP_STATUSES:
            existing_transcriptions = self.transcription_service.list_transcriptions(source_id)
            ready_transcription = next(
                (t for t in existing_transcriptions if t.get("status") == "READY"), None
            )
            if ready_transcription is not None:
                if tr_step.get("status") not in DONE_STEP_STATUSES:
                    tr_step["status"] = PipelineStepStatus.READY.value
                    tr_step["error"] = None
                    tr_step["completed_at"] = now
                transcript_id = ready_transcription.get("id")
            else:
                job_id = tr_step.get("job_id")
                if job_id:
                    try:
                        job = self.transcription_service.get_job(job_id)
                        jstatus = job.get("status")
                        if jstatus == MediaJobStatus.READY.value:
                            tr_step["status"] = PipelineStepStatus.READY.value
                            tr_step["error"] = None
                            tr_step["completed_at"] = now
                            transcript_id = job.get("transcription_id")
                        elif jstatus in {s.value for s in TRANSIENT_JOB_STATUSES}:
                            tr_step["status"] = (
                                PipelineStepStatus.WAITING_FOR_GPU.value
                                if jstatus == MediaJobStatus.WAITING_FOR_GPU.value
                                else PipelineStepStatus.RUNNING.value
                            )
                            prog = job.get("progress") or {}
                            pct = prog.get("percent")
                            tr_step["progress"] = float(pct) if isinstance(pct, (int, float)) else None
                            phase = prog.get("phase")
                            tr_step["message"] = phase or None
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
                if tr_step.get("status") in (PipelineStepStatus.PENDING.value, PipelineStepStatus.WAITING.value):
                    try:
                        job = self.transcription_service.start_transcription("twitch_vod", source_id)
                        tr_step["status"] = PipelineStepStatus.RUNNING.value
                        tr_step["job_id"] = job.get("id")
                        tr_step["error"] = None
                        if not tr_step.get("started_at"):
                            tr_step["started_at"] = now
                        transcript_id = job.get("transcription_id")
                    except Exception as exc:
                        tr_step["status"] = PipelineStepStatus.FAILED.value
                        tr_step["error"] = str(exc)
                        self._fail_run(run_id, f"Transcription step failed: {exc}")
                        self._save_run(run_id, steps=steps)
                        return

        # --- Step: CONVERSATION_MINING ---
        mining_step = step_by_type.get(PipelineStepType.CONVERSATION_MINING.value)
        if (
            mining_step
            and tr_step
            and tr_step.get("status") in DONE_STEP_STATUSES
            and self.mining_service is not None
        ):
            # Need a transcript_id to proceed.
            if transcript_id is None:
                # Re-derive from the transcription step.
                existing = self.transcription_service.list_transcriptions(source_id)
                ready_t = next((t for t in existing if t.get("status") == "READY"), None)
                if ready_t:
                    transcript_id = ready_t.get("id")
            if transcript_id:
                # Check for an existing completed mining run for this transcript.
                existing_mining = None
                try:
                    existing_mining = self.mining_service.get_latest_for_transcript(transcript_id)
                except Exception:
                    pass
                if (
                    existing_mining is not None
                    and existing_mining.get("status") == MiningRunStatus.COMPLETED
                    and int(existing_mining.get("transcript_revision") or 0)
                    == int((self.transcription_service.get_transcript_contract(transcript_id) or {}).get("revision") or 0)
                ):
                    # Reuse existing valid result.
                    if mining_step.get("status") not in DONE_STEP_STATUSES:
                        mining_step["status"] = PipelineStepStatus.SKIPPED.value
                        mining_step["error"] = None
                        mining_step["completed_at"] = now
                        mining_step["artifact_ids"] = [existing_mining.get("id") or ""]
                else:
                    # Check if a mining run is already active for this transcript.
                    mining_job_id = mining_step.get("job_id")
                    if mining_job_id:
                        try:
                            mining_run = self.mining_service.get_run(mining_job_id)
                            mstatus = mining_run.get("status")
                            if mstatus == MiningRunStatus.COMPLETED:
                                mining_step["status"] = PipelineStepStatus.READY.value
                                mining_step["error"] = None
                                mining_step["completed_at"] = now
                                mining_step["artifact_ids"] = [mining_run.get("id") or ""]
                            elif mstatus in (MiningRunStatus.QUEUED, MiningRunStatus.RUNNING):
                                mining_step["status"] = PipelineStepStatus.RUNNING.value
                                mining_step["progress"] = mining_run.get("progress") or 0.0
                                # Build a progress message.
                                blocks = mining_run.get("blocks") or []
                                done_blocks = sum(1 for b in blocks if b.get("status") in ("COMPLETED", "FAILED", "CANCELED"))
                                mining_step["message"] = f"Block {done_blocks} von {len(blocks)}"
                            elif mstatus == MiningRunStatus.FAILED:
                                mining_step["status"] = PipelineStepStatus.FAILED.value
                                mining_step["error"] = mining_run.get("error") or "mining failed"
                                self._fail_run(run_id, f"Conversation Mining failed: {mining_step['error']}")
                                self._save_run(run_id, steps=steps)
                                return
                            elif mstatus == MiningRunStatus.CANCELED:
                                mining_step["status"] = PipelineStepStatus.FAILED.value
                                mining_step["error"] = "mining was canceled"
                                self._fail_run(run_id, "Conversation Mining was canceled.")
                                self._save_run(run_id, steps=steps)
                                return
                        except ConversationMiningNotFoundError:
                            mining_step["job_id"] = None
                            mining_step["status"] = PipelineStepStatus.PENDING.value
                        except Exception as exc:
                            logger.warning("could not load mining run %s: %s", mining_job_id, exc)
                    if mining_step.get("status") in (PipelineStepStatus.PENDING.value, PipelineStepStatus.WAITING.value):
                        try:
                            mining_run = self.mining_service.start_run(source_id)
                            mining_step["status"] = PipelineStepStatus.RUNNING.value
                            mining_step["job_id"] = mining_run.get("id")
                            mining_step["error"] = None
                            if not mining_step.get("started_at"):
                                mining_step["started_at"] = now
                        except ConversationMiningUnavailableError as exc:
                            mining_step["status"] = PipelineStepStatus.FAILED.value
                            mining_step["error"] = str(exc)
                            self._fail_run(run_id, f"Conversation Mining unavailable: {exc}")
                            self._save_run(run_id, steps=steps)
                            return
                        except (ConversationMiningValidationError, ConversationMiningConflictError) as exc:
                            mining_step["status"] = PipelineStepStatus.FAILED.value
                            mining_step["error"] = str(exc)
                            self._fail_run(run_id, f"Conversation Mining failed: {exc}")
                            self._save_run(run_id, steps=steps)
                            return
            else:
                # No transcript id — cannot mine.
                if mining_step.get("status") not in DONE_STEP_STATUSES:
                    mining_step["status"] = PipelineStepStatus.SKIPPED.value
                    mining_step["error"] = None
                    mining_step["completed_at"] = now
        elif (
            mining_step
            and tr_step
            and tr_step.get("status") in DONE_STEP_STATUSES
            and self.mining_service is None
        ):
            # Mining service not wired — mark as NOT_IMPLEMENTED.
            if mining_step.get("status") not in DONE_STEP_STATUSES:
                mining_step["status"] = PipelineStepStatus.NOT_IMPLEMENTED.value
                mining_step["completed_at"] = now

        # --- Finalize ---
        all_done = all(s.get("status") in DONE_STEP_STATUSES for s in steps)
        any_waiting_gpu = any(s.get("status") == PipelineStepStatus.WAITING_FOR_GPU.value for s in steps)
        if all_done:
            run["status"] = PipelineStatus.COMPLETED.value
            run["completed_at"] = now
        elif any_waiting_gpu:
            run["status"] = PipelineStatus.WAITING_FOR_GPU.value
        else:
            run["status"] = PipelineStatus.RUNNING.value
        run["steps"] = steps
        run["updated_at"] = now
        run["progress"] = _compute_run_progress(run)
        run["current_step"] = _current_step(run)
        if library_item_id:
            run["library_item_id"] = library_item_id
        if transcript_id:
            run["transcript_id"] = transcript_id
        self.storage.save_run(run)

    # ------------------------------------------------------------------ helpers
    def _resolve_profile_id(self, uploader: str) -> Optional[str]:
        """Find an existing Twitch profile by login, or create one.

        Best-effort: never raises. If profile creation fails (e.g. conflict),
        the VOD is still imported without a profile.
        """
        login = uploader.strip().lower()
        if not login:
            return None
        try:
            for existing in self.vod_service.storage.iter_profiles():
                if existing.get("login", "").lower() == login:
                    return existing.get("id")
        except Exception:
            pass
        try:
            profile = self.vod_service.create_profile(login)
            return profile.get("id")
        except Exception:
            # Conflict or other error: try to find it again (race).
            try:
                for existing in self.vod_service.storage.iter_profiles():
                    if existing.get("login", "").lower() == login:
                        return existing.get("id")
            except Exception:
                return None
            return None

    def _save_run(self, run_id: str, steps: list[dict]) -> None:
        run = self.storage.load_run(run_id)
        run["steps"] = steps
        run["updated_at"] = _now_iso()
        run["progress"] = _compute_run_progress(run)
        run["current_step"] = _current_step(run)
        self.storage.save_run(run)

    def _fail_run(self, run_id: str, reason: str) -> None:
        run = self.storage.load_run(run_id)
        run["status"] = PipelineStatus.FAILED.value
        run["error"] = reason
        run["completed_at"] = _now_iso()
        run["updated_at"] = _now_iso()
        run["progress"] = _compute_run_progress(run)
        self.storage.save_run(run)

    def _recover_on_startup(self) -> None:
        """Recover active runs after a server restart.

        For each active run we re-evaluate the actual state of its
        underlying jobs and artifacts via the orchestrator. We do NOT
        blindly mark active runs as FAILED — instead we let the
        orchestrator reconcile:

        * if a step's job is gone but its artifact exists, the step is
          marked READY;
        * if a step's job is gone and no artifact exists, the step is
          reset to PENDING so the orchestrator re-starts it;
        * a run whose VOD no longer exists is marked FAILED.

        This avoids both "stuck RUNNING" runs and silent re-processing.
        """
        now = _now_iso()
        for run in list(self.storage.iter_runs()):
            if run.get("status") not in ACTIVE_PIPELINE_STATUSES:
                continue
            run_id = run["id"]
            source_id = run.get("source_id")
            # If the VOD is gone, the run cannot continue.
            try:
                self.vod_service.storage.load_vod(source_id)
            except Exception:
                self._fail_run(run_id, "VOD no longer exists after restart.")
                continue
            steps = run.get("steps") or []
            for step in steps:
                st = step.get("status")
                if st in DONE_STEP_STATUSES or st == PipelineStepStatus.PENDING.value:
                    continue
                # A step that was RUNNING/WAITING_FOR_GPU before the restart
                # may have an orphaned job. Reconcile against artifacts.
                step_type = step.get("type")
                job_id = step.get("job_id")
                if step_type == PipelineStepType.DOWNLOAD.value:
                    try:
                        vod = self.vod_service.storage.load_vod(source_id)
                        if vod.get("status") == VodStatus.READY.value:
                            step["status"] = PipelineStepStatus.READY.value
                            step["error"] = None
                            step["completed_at"] = now
                        else:
                            # Reset so the orchestrator re-evaluates / re-starts.
                            step["status"] = PipelineStepStatus.PENDING.value
                            step["job_id"] = None
                            step["progress"] = None
                    except Exception:
                        step["status"] = PipelineStepStatus.FAILED.value
                        step["error"] = "VOD disappeared during recovery"
                elif step_type == PipelineStepType.EXTRACT_AUDIO.value:
                    art = self.audio_service.get_audio_artifact(source_id)
                    if art is not None:
                        step["status"] = PipelineStepStatus.READY.value
                        step["error"] = None
                        step["completed_at"] = now
                        step["job_id"] = art.get("produced_by_job_id")
                    else:
                        # If the job still exists and is active, leave it;
                        # otherwise reset to PENDING.
                        alive = False
                        if job_id:
                            try:
                                job = self.audio_service.get_job(job_id)
                                if job.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES}:
                                    alive = True
                            except Exception:
                                alive = False
                        if not alive:
                            step["status"] = PipelineStepStatus.PENDING.value
                            step["job_id"] = None
                            step["progress"] = None
                elif step_type == PipelineStepType.TRANSCRIBE.value:
                    existing = self.transcription_service.list_transcriptions(source_id)
                    ready = next((t for t in existing if t.get("status") == "READY"), None)
                    if ready is not None:
                        step["status"] = PipelineStepStatus.READY.value
                        step["error"] = None
                        step["completed_at"] = now
                    else:
                        alive = False
                        if job_id:
                            try:
                                job = self.transcription_service.get_job(job_id)
                                if job.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES}:
                                    alive = True
                            except Exception:
                                alive = False
                        if not alive:
                            step["status"] = PipelineStepStatus.PENDING.value
                            step["job_id"] = None
                            step["progress"] = None
                elif step_type == PipelineStepType.CONVERSATION_MINING.value:
                    # The mining service has its own recovery. Reset the
                    # pipeline step to PENDING so the orchestrator
                    # re-evaluates the mining run state.
                    if self.mining_service is not None and job_id:
                        try:
                            mining_run = self.mining_service.get_run(job_id)
                            mstatus = mining_run.get("status")
                            if mstatus == MiningRunStatus.COMPLETED:
                                step["status"] = PipelineStepStatus.READY.value
                                step["error"] = None
                                step["completed_at"] = now
                            elif mstatus in (MiningRunStatus.FAILED, MiningRunStatus.CANCELED, MiningRunStatus.STALE):
                                step["status"] = PipelineStepStatus.PENDING.value
                                step["job_id"] = None
                                step["progress"] = None
                            # If QUEUED/RUNNING, the mining service recovery
                            # will have marked it FAILED; leave the pipeline
                            # step as-is so the orchestrator picks it up.
                        except Exception:
                            step["status"] = PipelineStepStatus.PENDING.value
                            step["job_id"] = None
                            step["progress"] = None
                    else:
                        step["status"] = PipelineStepStatus.PENDING.value
                        step["job_id"] = None
                        step["progress"] = None
            run["steps"] = steps
            run["updated_at"] = now
            run["progress"] = _compute_run_progress(run)
            run["current_step"] = _current_step(run)
            self.storage.save_run(run)
        # Start the orchestrator if any runs remain active.
        active = any(
            r.get("status") in ACTIVE_PIPELINE_STATUSES
            for r in self.storage.iter_runs()
        )
        if active:
            self._ensure_orchestrator()
