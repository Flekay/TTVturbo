"""Video region cut service (ausschneiden).

Cuts a rectangular area out of a source video and produces a new video that
contains only the selected region.  Audio is preserved from the source.
The default output lifecycle is ``TEMPORARY`` because the typical use case is
a quick camera-region extraction from a VOD.
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from ttvturbo.media_capabilities import (
    SubprocessCapabilityService,
    now_iso,
    register_derived_library_item,
    resolve_library_media,
    sha256_file,
)
from ttvturbo.settings import Settings

from .schemas import (
    CutResult,
    StartCutRequest,
    VideoCutConflictError,
    VideoCutNotFoundError,
    VideoCutUnavailableError,
    VideoCutValidationError,
)
from .storage import VideoCutStorage

OPERATION = "video_cut"


class VideoCutService(SubprocessCapabilityService):
    def __init__(
        self,
        *,
        storage: VideoCutStorage,
        library_service: Any,
        settings: Settings,
        gpu_lock: Any,
        worker_python: Optional[str] = None,
        ffmpeg_path: Optional[str] = None,
        ffprobe_path: Optional[str] = None,
        worker_runner=None,
    ) -> None:
        self.library_service = library_service
        self.settings = settings
        self.gpu_lock = gpu_lock
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe") or "ffprobe"
        self._runtime_cache: tuple[float, dict[str, Any]] | None = None
        super().__init__(
            storage=storage,
            operation=OPERATION,
            worker_module="ttvturbo.video_cut.worker",
            worker_python=worker_python,
            max_concurrent=settings.video_cut_max_concurrent,
            worker_runner=worker_runner,
        )

    def runtime_status(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._runtime_cache and now - self._runtime_cache[0] < 10:
            return dict(self._runtime_cache[1])
        ffmpeg_ok = bool(shutil.which(self.ffmpeg_path) or Path(self.ffmpeg_path).is_file())
        ffprobe_ok = bool(shutil.which(self.ffprobe_path) or Path(self.ffprobe_path).is_file())
        reasons = []
        if not ffmpeg_ok:
            reasons.append("ffmpeg unavailable")
        if not ffprobe_ok:
            reasons.append("ffprobe unavailable")
        result = {
            "available": ffmpeg_ok and ffprobe_ok,
            "configured": True,
            "busy": bool(self.gpu_lock and self.gpu_lock.current_owner()),
            "ffmpeg_available": ffmpeg_ok,
            "ffprobe_available": ffprobe_ok,
            "reasons": reasons,
            "error": reasons[0] if reasons else None,
        }
        self._runtime_cache = (now, result)
        return dict(result)

    def capabilities(self) -> dict[str, Any]:
        status = self.runtime_status()
        return {
            "id": OPERATION,
            "available": status["available"],
            "supports_time_range": True,
            "supports_static_region": True,
            "supports_region_track": False,
            "default_output_lifecycle": "TEMPORARY",
            "preserves_audio": True,
            "reasons": status["reasons"],
        }

    def start_job(self, **payload: Any) -> dict[str, Any]:
        try:
            request = StartCutRequest.model_validate(payload)
        except ValidationError as exc:
            raise VideoCutValidationError(str(exc)) from exc
        try:
            meta, source_path = resolve_library_media(self.library_service, request.media_item_id, request.asset_id)
        except Exception as exc:
            raise VideoCutNotFoundError(str(exc)) from exc

        job_id = str(uuid.uuid4())
        options = request.options.model_dump(mode="json")
        job = {
            "schema_version": 1,
            "id": job_id,
            "operation": OPERATION,
            "status": "QUEUED",
            "progress": 0.0,
            "current_stage": None,
            "created_at": now_iso(),
            "started_at": None,
            "completed_at": None,
            "media_item_id": request.media_item_id,
            "asset_id": request.asset_id,
            "source_title": meta.get("title"),
            "source_sha256": sha256_file(source_path),
            "start_us": request.start_us,
            "end_us": request.end_us,
            "region": request.region.model_dump(),
            "output_lifecycle": request.output_lifecycle,
            "options": options,
            "attempt": 1,
            "output_artifact_id": None,
            "library_item_id": None,
            "error": None,
        }
        worker_job = {
            "job_id": job_id,
            "source_path": str(source_path),
            "source_sha256": job["source_sha256"],
            "start_seconds": request.start_us / 1_000_000,
            "end_seconds": request.end_us / 1_000_000 if request.end_us is not None else None,
            "region": job["region"],
            "options": options,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
        }
        try:
            return self._start_prepared_job(job, worker_job)
        except RuntimeError as exc:
            if "unavailable" in str(exc):
                raise VideoCutUnavailableError(str(exc)) from exc
            raise VideoCutConflictError(str(exc)) from exc

    def _finalize_job(self, job_id: str) -> None:
        job = self.storage.load_job(job_id)
        if job.get("status") != "COMPLETED" or job.get("output_artifact_id"):
            return
        result_payload = self.storage.load_result(job_id)
        if not result_payload or not result_payload.get("success"):
            raise RuntimeError("video-cut worker result is missing")
        result = CutResult.model_validate(result_payload)
        output_path = self.storage.job_dir(job_id) / result.output_file
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError("video-cut output is missing")
        artifact_id = str(uuid.uuid4())
        artifact = {
            "schema_version": 1,
            "id": artifact_id,
            "job_id": job_id,
            "artifact_type": "VIDEO_REGION_CUT",
            "source_media_item_id": job["media_item_id"],
            "source_asset_id": job.get("asset_id"),
            "source_sha256": job["source_sha256"],
            "effective_options": result.effective_options,
            "source_resolution": result.source_resolution,
            "output_resolution": result.output_resolution,
            "duration_seconds": result.duration_seconds,
            "fps": result.fps,
            "created_at": now_iso(),
            "revision": 1,
        }
        item_id, dest = register_derived_library_item(
            self.library_service,
            output_path=output_path,
            title=f"Cut – {job.get('source_title') or job['media_item_id']}",
            duration_seconds=result.duration_seconds,
            operation=OPERATION,
            source_media_item_id=job["media_item_id"],
            artifact_id=artifact_id,
            container="mp4",
            metadata={"job_id": job_id, "options": result.effective_options},
            lifecycle=job.get("output_lifecycle", "TEMPORARY"),
        )
        artifact.update({
            "library_item_id": item_id,
            "file_name": dest.name,
            "file_size_bytes": dest.stat().st_size,
            "lifecycle": job.get("output_lifecycle", "TEMPORARY"),
        })
        self.storage.save_artifact(artifact)
        job["output_artifact_id"] = artifact_id
        job["library_item_id"] = item_id
        self.storage.save_job(job)
