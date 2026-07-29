"""Generic video background-removal service."""
from __future__ import annotations

import importlib.util
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from ttvturbo.media_capabilities import SubprocessCapabilityService, now_iso, register_derived_library_item, resolve_library_media, sha256_file
from ttvturbo.settings import Settings

from .schemas import (
    BackgroundRemovalConflictError,
    BackgroundRemovalNotFoundError,
    BackgroundRemovalResult,
    BackgroundRemovalUnavailableError,
    BackgroundRemovalValidationError,
    ForegroundMode,
    StartBackgroundRemovalRequest,
)
from .storage import VideoBackgroundRemovalStorage

OPERATION = "video_background_removal"


class VideoBackgroundRemovalService(SubprocessCapabilityService):
    def __init__(
        self,
        *,
        storage: VideoBackgroundRemovalStorage,
        library_service: Any,
        settings: Settings,
        gpu_lock: Any,
        visual_analysis_service: Optional[Any] = None,
        worker_python: Optional[str] = None,
        ffmpeg_path: Optional[str] = None,
        ffprobe_path: Optional[str] = None,
        worker_runner=None,
    ) -> None:
        self.library_service = library_service
        self.settings = settings
        self.gpu_lock = gpu_lock
        self.visual_analysis_service = visual_analysis_service
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe") or "ffprobe"
        self._runtime_cache: tuple[float, dict[str, Any]] | None = None
        super().__init__(
            storage=storage,
            operation=OPERATION,
            worker_module="ttvturbo.video_background_removal.worker",
            worker_python=worker_python,
            max_concurrent=settings.video_background_removal_max_concurrent,
            worker_runner=worker_runner,
        )

    def runtime_status(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._runtime_cache and now - self._runtime_cache[0] < 10:
            return dict(self._runtime_cache[1])
        ffmpeg_ok = bool(shutil.which(self.ffmpeg_path) or Path(self.ffmpeg_path).is_file())
        ffprobe_ok = bool(shutil.which(self.ffprobe_path) or Path(self.ffprobe_path).is_file())
        rembg_ok = importlib.util.find_spec("rembg") is not None
        numpy_ok = importlib.util.find_spec("numpy") is not None
        pillow_ok = importlib.util.find_spec("PIL") is not None
        reasons = []
        if not ffmpeg_ok: reasons.append("ffmpeg unavailable")
        if not ffprobe_ok: reasons.append("ffprobe unavailable")
        if not rembg_ok: reasons.append("rembg unavailable")
        if not numpy_ok: reasons.append("numpy unavailable")
        if not pillow_ok: reasons.append("Pillow unavailable")
        result = {
            "available": ffmpeg_ok and ffprobe_ok and rembg_ok and numpy_ok and pillow_ok,
            "configured": bool(self.settings.video_background_removal_model_id),
            "model_id": self.settings.video_background_removal_model_id,
            "person_model_id": self.settings.video_background_removal_person_model_id,
            "device": self.settings.video_background_removal_device,
            "busy": bool(self.gpu_lock and self.gpu_lock.current_owner()),
            "reasons": reasons,
            "error": reasons[0] if reasons else None,
        }
        if not result["configured"]:
            result["available"] = False
            result["reasons"].append("background-removal model not configured")
            result["error"] = result["reasons"][0]
        self._runtime_cache = (now, result)
        return dict(result)

    def capabilities(self) -> dict[str, Any]:
        status = self.runtime_status()
        return {
            "id": OPERATION,
            "available": status["available"],
            "modes": [x.value for x in ForegroundMode],
            "output_modes": ["ALPHA_MASK", "TRANSPARENT_VIDEO", "COMPOSITED_VIDEO"],
            "background_modes": ["TRANSPARENT", "SOLID_COLOR", "BLURRED_ORIGINAL", "IMAGE_ASSET", "VIDEO_ASSET"],
            "supports_time_range": True,
            "supports_region_track": True,
            "supports_temporal_smoothing": True,
            "reasons": status["reasons"],
        }

    def start_job(self, **payload: Any) -> dict[str, Any]:
        try:
            request = StartBackgroundRemovalRequest.model_validate(payload)
        except ValidationError as exc:
            raise BackgroundRemovalValidationError(str(exc)) from exc
        try:
            source_meta, source_path = resolve_library_media(self.library_service, request.media_item_id, request.asset_id)
        except Exception as exc:
            raise BackgroundRemovalNotFoundError(str(exc)) from exc

        region_track = None
        if request.region_track_artifact_id:
            if self.visual_analysis_service is None:
                raise BackgroundRemovalUnavailableError("visual analysis service is not configured")
            try:
                artifact = self.visual_analysis_service.get_artifact(request.region_track_artifact_id)
            except Exception as exc:
                raise BackgroundRemovalNotFoundError(str(exc)) from exc
            if artifact.get("media_item_id") != request.media_item_id:
                raise BackgroundRemovalValidationError("region track belongs to another media item")
            region_track = next((x for x in artifact.get("region_tracks", []) if x.get("id") == request.region_track_id), None)
            if region_track is None:
                raise BackgroundRemovalValidationError(f"region track not found: {request.region_track_id}")

        background_path = None
        background_meta = None
        if request.background.image_asset_id:
            try: background_meta, background_path = resolve_library_media(self.library_service, request.background.image_asset_id)
            except Exception as exc: raise BackgroundRemovalNotFoundError(str(exc)) from exc
        elif request.background.video_asset_id:
            try: background_meta, background_path = resolve_library_media(self.library_service, request.background.video_asset_id)
            except Exception as exc: raise BackgroundRemovalNotFoundError(str(exc)) from exc

        job_id = str(uuid.uuid4())
        output_modes = [x.value for x in request.output_modes]
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
            "source_title": source_meta.get("title"),
            "source_sha256": sha256_file(source_path),
            "start_us": request.start_us,
            "end_us": request.end_us,
            "mode": request.mode.value,
            "region": request.region.model_dump() if request.region else None,
            "region_track_artifact_id": request.region_track_artifact_id,
            "region_track_id": request.region_track_id,
            "output_modes": output_modes,
            "background": request.background.model_dump(mode="json"),
            "temporal_smoothing": request.temporal_smoothing,
            "edge_refinement": request.edge_refinement,
            "preserve_audio": request.preserve_audio,
            "output_lifecycle": request.output_lifecycle,
            "attempt": 1,
            "output_artifact_id": None,
            "output_artifact_ids": [],
            "library_item_id": None,
            "library_item_ids": [],
            "error": None,
        }
        effective_model_id = (
            self.settings.video_background_removal_person_model_id
            if request.mode == ForegroundMode.PERSON
            else self.settings.video_background_removal_model_id
        )
        worker_job = {
            "job_id": job_id,
            "source_path": str(source_path),
            "source_sha256": job["source_sha256"],
            "start_seconds": request.start_us / 1_000_000,
            "end_seconds": request.end_us / 1_000_000 if request.end_us is not None else None,
            "mode": request.mode.value,
            "region": job["region"],
            "region_track": region_track,
            "output_modes": output_modes,
            "background": job["background"],
            "background_path": str(background_path) if background_path else None,
            "temporal_smoothing": request.temporal_smoothing,
            "edge_refinement": request.edge_refinement,
            "preserve_audio": request.preserve_audio,
            "model_id": effective_model_id,
            "device": self.settings.video_background_removal_device,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "data_root": str(self.settings.data_root),
            "gpu_lock_stale_seconds": self.settings.gpu_lock_stale_seconds,
        }
        try:
            return self._start_prepared_job(job, worker_job)
        except RuntimeError as exc:
            if "unavailable" in str(exc): raise BackgroundRemovalUnavailableError(str(exc)) from exc
            raise BackgroundRemovalConflictError(str(exc)) from exc

    def _finalize_job(self, job_id: str) -> None:
        job = self.storage.load_job(job_id)
        if job.get("status") != "COMPLETED" or job.get("output_artifact_ids"):
            return
        payload = self.storage.load_result(job_id)
        if not payload or not payload.get("success"):
            raise RuntimeError("background-removal result is missing")
        result = BackgroundRemovalResult.model_validate(payload)
        artifact_ids: list[str] = []
        item_ids: list[str] = []
        for output in result.outputs:
            path = self.storage.job_dir(job_id) / output["file_name"]
            if not path.is_file() or path.stat().st_size <= 0:
                raise RuntimeError(f"output is missing: {output['file_name']}")
            artifact_id = str(uuid.uuid4())
            container = output["container"]
            item_id, dest = register_derived_library_item(
                self.library_service,
                output_path=path,
                title=f"Background removed – {job.get('source_title') or job['media_item_id']} ({output['type']})",
                duration_seconds=result.duration_seconds,
                operation=OPERATION,
                source_media_item_id=job["media_item_id"],
                artifact_id=artifact_id,
                container=container,
                metadata={"job_id": job_id, "output_type": output["type"], "model_id": result.model_id},
                lifecycle=job.get("output_lifecycle", "PERSISTENT"),
            )
            artifact = {
                "schema_version": 1,
                "id": artifact_id,
                "job_id": job_id,
                "artifact_type": output["type"],
                "source_media_item_id": job["media_item_id"],
                "source_asset_id": job.get("asset_id"),
                "source_sha256": job["source_sha256"],
                "model_id": result.model_id,
                "effective_options": result.effective_options,
                "source_resolution": result.source_resolution,
                "output_resolution": result.output_resolution,
                "duration_seconds": result.duration_seconds,
                "fps": result.fps,
                "library_item_id": item_id,
                "file_name": dest.name,
                "file_size_bytes": dest.stat().st_size,
                "container": container,
                "lifecycle": job.get("output_lifecycle", "PERSISTENT"),
                "created_at": now_iso(),
                "revision": 1,
            }
            self.storage.save_artifact(artifact)
            artifact_ids.append(artifact_id)
            item_ids.append(item_id)
        job["output_artifact_ids"] = artifact_ids
        job["output_artifact_id"] = artifact_ids[0]
        job["library_item_ids"] = item_ids
        job["library_item_id"] = item_ids[0]
        self.storage.save_job(job)
