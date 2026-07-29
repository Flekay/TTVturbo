"""Reusable video upscale service."""
from __future__ import annotations

import importlib.util
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
    StartUpscaleRequest,
    UpscaleResult,
    VideoUpscaleConflictError,
    VideoUpscaleNotFoundError,
    VideoUpscaleUnavailableError,
    VideoUpscaleValidationError,
)
from .storage import VideoUpscaleStorage

OPERATION = "video_upscale"


class VideoUpscaleService(SubprocessCapabilityService):
    def __init__(
        self,
        *,
        storage: VideoUpscaleStorage,
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
        configured_realesrgan = settings.video_upscale_realesrgan_path.strip()
        self.realesrgan_path = (
            shutil.which(configured_realesrgan) if configured_realesrgan else None
        ) or (Path(configured_realesrgan) if configured_realesrgan and Path(configured_realesrgan).is_file() else None)
        if self.realesrgan_path is None:
            discovered = shutil.which("realesrgan-ncnn-vulkan")
            self.realesrgan_path = Path(discovered) if discovered else None
        else:
            self.realesrgan_path = Path(self.realesrgan_path)
        self._runtime_cache: tuple[float, dict[str, Any]] | None = None
        super().__init__(
            storage=storage,
            operation=OPERATION,
            worker_module="ttvturbo.video_upscale.worker",
            worker_python=worker_python,
            max_concurrent=settings.video_upscale_max_concurrent,
            worker_runner=worker_runner,
        )

    def runtime_status(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._runtime_cache and now - self._runtime_cache[0] < 10:
            return dict(self._runtime_cache[1])
        ffmpeg_ok = bool(shutil.which(self.ffmpeg_path) or Path(self.ffmpeg_path).is_file())
        ffprobe_ok = bool(shutil.which(self.ffprobe_path) or Path(self.ffprobe_path).is_file())
        pillow_ok = importlib.util.find_spec("PIL") is not None
        realesrgan_ok = bool(self.realesrgan_path and self.realesrgan_path.is_file())
        reasons = []
        if not ffmpeg_ok: reasons.append("ffmpeg unavailable")
        if not ffprobe_ok: reasons.append("ffprobe unavailable")
        if not pillow_ok: reasons.append("Pillow unavailable")
        result = {
            "available": ffmpeg_ok and ffprobe_ok and pillow_ok,
            "configured": True,
            "busy": bool(self.gpu_lock and self.gpu_lock.current_owner()),
            "ffmpeg_available": ffmpeg_ok,
            "ffprobe_available": ffprobe_ok,
            "lanczos_available": pillow_ok,
            "realesrgan_available": realesrgan_ok,
            "default_engine": self.settings.video_upscale_backend,
            "realesrgan_model": self.settings.video_upscale_realesrgan_model,
            "reasons": reasons,
            "error": reasons[0] if reasons else None,
        }
        self._runtime_cache = (now, result)
        return dict(result)

    def capabilities(self) -> dict[str, Any]:
        status = self.runtime_status()
        engines = ["LANCZOS"]
        if status["realesrgan_available"]:
            engines.append("REALESRGAN")
        return {
            "id": OPERATION,
            "available": status["available"],
            "engines": engines,
            "scale_factors": [2, 4],
            "supports_custom_resolution": True,
            "supports_time_range": True,
            "supports_static_region": True,
            "supports_region_track": True,
            "quality_profiles": ["PREVIEW", "FINAL"],
            "reasons": status["reasons"],
        }

    def start_job(self, **payload: Any) -> dict[str, Any]:
        try:
            request = StartUpscaleRequest.model_validate(payload)
        except ValidationError as exc:
            raise VideoUpscaleValidationError(str(exc)) from exc
        try:
            meta, source_path = resolve_library_media(self.library_service, request.media_item_id, request.asset_id)
        except Exception as exc:
            raise VideoUpscaleNotFoundError(str(exc)) from exc
        region_track = None
        if request.region_track_artifact_id:
            if self.visual_analysis_service is None:
                raise VideoUpscaleUnavailableError("visual analysis service is not configured")
            try:
                artifact = self.visual_analysis_service.get_artifact(request.region_track_artifact_id)
            except Exception as exc:
                raise VideoUpscaleNotFoundError(str(exc)) from exc
            region_track = next((x for x in artifact.get("region_tracks", []) if x.get("id") == request.region_track_id), None)
            if region_track is None:
                raise VideoUpscaleValidationError(f"region track not found: {request.region_track_id}")
            if artifact.get("media_item_id") != request.media_item_id:
                raise VideoUpscaleValidationError("region track belongs to a different media item")

        job_id = str(uuid.uuid4())
        options = request.options.model_dump(mode="json")
        if options["engine"] == "AUTO":
            options["engine"] = self.settings.video_upscale_backend.upper()
            if options["engine"] not in {"AUTO", "LANCZOS", "REALESRGAN"}:
                options["engine"] = "AUTO"
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
            "region": request.region.model_dump() if request.region else None,
            "region_track_artifact_id": request.region_track_artifact_id,
            "region_track_id": request.region_track_id,
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
            "region_track": region_track,
            "options": options,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "data_root": str(self.settings.data_root),
            "gpu_lock_stale_seconds": self.settings.gpu_lock_stale_seconds,
            "realesrgan_path": str(self.realesrgan_path) if self.realesrgan_path else "",
            "realesrgan_model": self.settings.video_upscale_realesrgan_model,
        }
        try:
            return self._start_prepared_job(job, worker_job)
        except RuntimeError as exc:
            if "unavailable" in str(exc):
                raise VideoUpscaleUnavailableError(str(exc)) from exc
            raise VideoUpscaleConflictError(str(exc)) from exc

    def _finalize_job(self, job_id: str) -> None:
        job = self.storage.load_job(job_id)
        if job.get("status") != "COMPLETED" or job.get("output_artifact_id"):
            return
        result_payload = self.storage.load_result(job_id)
        if not result_payload or not result_payload.get("success"):
            raise RuntimeError("upscale worker result is missing")
        result = UpscaleResult.model_validate(result_payload)
        output_path = self.storage.job_dir(job_id) / result.output_file
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError("upscale output is missing")
        artifact_id = str(uuid.uuid4())
        artifact = {
            "schema_version": 1,
            "id": artifact_id,
            "job_id": job_id,
            "artifact_type": "VIDEO_REGION_UPSCALED" if job.get("region") or job.get("region_track_id") else ("VIDEO_UPSCALE_PREVIEW" if job["options"].get("quality") == "PREVIEW" else "VIDEO_UPSCALED"),
            "source_media_item_id": job["media_item_id"],
            "source_asset_id": job.get("asset_id"),
            "source_sha256": job["source_sha256"],
            "engine": result.engine,
            "model_id": result.model_id,
            "model_version": result.model_version,
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
            title=f"Upscaled – {job.get('source_title') or job['media_item_id']}",
            duration_seconds=result.duration_seconds,
            operation=OPERATION,
            source_media_item_id=job["media_item_id"],
            artifact_id=artifact_id,
            container="mp4",
            metadata={"job_id": job_id, "engine": result.engine, "options": result.effective_options},
            lifecycle=job.get("output_lifecycle", "PERSISTENT"),
        )
        artifact.update({
            "library_item_id": item_id,
            "file_name": dest.name,
            "file_size_bytes": dest.stat().st_size,
            "lifecycle": job.get("output_lifecycle", "PERSISTENT"),
        })
        self.storage.save_artifact(artifact)
        job["output_artifact_id"] = artifact_id
        job["library_item_id"] = item_id
        self.storage.save_job(job)
