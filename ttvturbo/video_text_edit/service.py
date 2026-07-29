"""Text-guided video edit/inpaint service."""
from __future__ import annotations

import importlib.util
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from ttvturbo.media_capabilities import SubprocessCapabilityService, now_iso, register_derived_library_item, resolve_library_media, sha256_file
from ttvturbo.settings import Settings

from .schemas import (
    StartVideoTextEditRequest,
    TextEditMode,
    TextEditResult,
    VideoTextEditConflictError,
    VideoTextEditNotFoundError,
    VideoTextEditUnavailableError,
    VideoTextEditValidationError,
)
from .storage import VideoTextEditStorage

OPERATION = "video_text_edit"


class VideoTextEditService(SubprocessCapabilityService):
    def __init__(
        self,
        *,
        storage: VideoTextEditStorage,
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
            worker_module="ttvturbo.video_text_edit.worker",
            worker_python=worker_python,
            max_concurrent=settings.video_text_edit_max_concurrent,
            worker_runner=worker_runner,
        )

    def runtime_status(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._runtime_cache and now - self._runtime_cache[0] < 10:
            return dict(self._runtime_cache[1])
        ffmpeg_ok = bool(shutil.which(self.ffmpeg_path) or Path(self.ffmpeg_path).is_file())
        ffprobe_ok = bool(shutil.which(self.ffprobe_path) or Path(self.ffprobe_path).is_file())
        deps = {name: importlib.util.find_spec(name) is not None for name in ("torch", "diffusers", "PIL", "numpy")}
        cuda = False
        if deps["torch"]:
            try:
                import torch
                cuda = bool(torch.cuda.is_available())
            except Exception:
                pass
        device = self.settings.video_text_edit_device
        model_configured = bool(self.settings.video_text_inpaint_model_id and self.settings.video_instruction_edit_model_id)
        reasons = []
        if not ffmpeg_ok: reasons.append("ffmpeg unavailable")
        if not ffprobe_ok: reasons.append("ffprobe unavailable")
        for name, ok in deps.items():
            if not ok: reasons.append(f"{name} unavailable")
        if device.startswith("cuda") and not cuda: reasons.append("CUDA unavailable")
        if not model_configured: reasons.append("video edit models are not configured")
        result = {
            "available": ffmpeg_ok and ffprobe_ok and all(deps.values()) and model_configured and (not device.startswith("cuda") or cuda),
            "configured": model_configured,
            "device": device,
            "inpaint_model_id": self.settings.video_text_inpaint_model_id,
            "instruction_model_id": self.settings.video_instruction_edit_model_id,
            "max_processing_side": int(self.settings.video_text_edit_max_processing_side),
            "busy": bool(self.gpu_lock and self.gpu_lock.current_owner()),
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
            "modes": [x.value for x in TextEditMode],
            "mask_modes": ["FULL_FRAME", "STATIC_REGION", "REGION_TRACK", "MASK_ASSET"],
            "supports_time_range": True,
            "supports_audio_preservation": True,
            "supports_temporal_consistency": True,
            "quality_profiles": ["PREVIEW", "FINAL"],
            "reasons": status["reasons"],
        }

    def start_job(self, **payload: Any) -> dict[str, Any]:
        try:
            request = StartVideoTextEditRequest.model_validate(payload)
        except ValidationError as exc:
            raise VideoTextEditValidationError(str(exc)) from exc
        try:
            source_meta, source_path = resolve_library_media(self.library_service, request.media_item_id, request.asset_id)
        except Exception as exc:
            raise VideoTextEditNotFoundError(str(exc)) from exc

        region_track = None
        if request.region_track_artifact_id:
            if self.visual_analysis_service is None:
                raise VideoTextEditUnavailableError("visual analysis service is not configured")
            try:
                artifact = self.visual_analysis_service.get_artifact(request.region_track_artifact_id)
            except Exception as exc:
                raise VideoTextEditNotFoundError(str(exc)) from exc
            if artifact.get("media_item_id") != request.media_item_id:
                raise VideoTextEditValidationError("region track belongs to another media item")
            region_track = next((x for x in artifact.get("region_tracks", []) if x.get("id") == request.region_track_id), None)
            if region_track is None:
                raise VideoTextEditValidationError(f"region track not found: {request.region_track_id}")

        mask_path = None
        if request.mask_asset_id:
            try:
                _, mask_path = resolve_library_media(self.library_service, request.mask_asset_id)
            except Exception as exc:
                raise VideoTextEditNotFoundError(str(exc)) from exc

        seed = request.options.seed if request.options.seed is not None else secrets.randbits(63)
        model_id = self.settings.video_text_inpaint_model_id if request.mode == TextEditMode.TEXT_INPAINT else self.settings.video_instruction_edit_model_id
        job_id = str(uuid.uuid4())
        options = request.options.model_dump(mode="json")
        options["seed"] = seed
        max_processing_side = int(self.settings.video_text_edit_max_processing_side)
        if options.get("quality") == "PREVIEW":
            options["num_inference_steps"] = min(int(options["num_inference_steps"]), 12)
            max_processing_side = min(max_processing_side, 512)
        options["max_processing_side"] = max_processing_side
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
            "mode": request.mode.value,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "start_us": request.start_us,
            "end_us": request.end_us,
            "mask_mode": request.mask_mode.value,
            "region": request.region.model_dump() if request.region else None,
            "region_track_artifact_id": request.region_track_artifact_id,
            "region_track_id": request.region_track_id,
            "mask_asset_id": request.mask_asset_id,
            "model_id": model_id,
            "seed": seed,
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
            "mode": request.mode.value,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "start_seconds": request.start_us / 1_000_000,
            "end_seconds": request.end_us / 1_000_000 if request.end_us is not None else None,
            "mask_mode": request.mask_mode.value,
            "region": job["region"],
            "region_track": region_track,
            "mask_path": str(mask_path) if mask_path else None,
            "model_id": model_id,
            "device": self.settings.video_text_edit_device,
            "dtype": self.settings.video_text_edit_dtype,
            "cache_dir": self.settings.video_text_edit_cache_dir,
            "max_processing_side": max_processing_side,
            "seed": seed,
            "options": options,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "data_root": str(self.settings.data_root),
            "gpu_lock_stale_seconds": self.settings.gpu_lock_stale_seconds,
        }
        try:
            return self._start_prepared_job(job, worker_job)
        except RuntimeError as exc:
            if "unavailable" in str(exc): raise VideoTextEditUnavailableError(str(exc)) from exc
            raise VideoTextEditConflictError(str(exc)) from exc

    def _finalize_job(self, job_id: str) -> None:
        job = self.storage.load_job(job_id)
        if job.get("status") != "COMPLETED" or job.get("output_artifact_id"):
            return
        payload = self.storage.load_result(job_id)
        if not payload or not payload.get("success"):
            raise RuntimeError("video edit result is missing")
        result = TextEditResult.model_validate(payload)
        output = self.storage.job_dir(job_id) / result.output_file
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("video edit output is missing")
        artifact_id = str(uuid.uuid4())
        item_id, dest = register_derived_library_item(
            self.library_service,
            output_path=output,
            title=f"Text edited – {job.get('source_title') or job['media_item_id']}",
            duration_seconds=result.duration_seconds,
            operation=OPERATION,
            source_media_item_id=job["media_item_id"],
            artifact_id=artifact_id,
            container="mp4",
            metadata={"job_id": job_id, "mode": result.mode, "model_id": result.model_id, "prompt": job["prompt"]},
        )
        artifact = {
            "schema_version": 1,
            "id": artifact_id,
            "job_id": job_id,
            "artifact_type": "VIDEO_TEXT_INPAINTED" if result.mode == "TEXT_INPAINT" else "VIDEO_TEXT_EDITED",
            "source_media_item_id": job["media_item_id"],
            "source_asset_id": job.get("asset_id"),
            "source_sha256": job["source_sha256"],
            "mode": result.mode,
            "prompt": job["prompt"],
            "negative_prompt": job.get("negative_prompt"),
            "model_id": result.model_id,
            "model_revision": result.model_revision,
            "seed": result.seed,
            "effective_options": result.effective_options,
            "source_resolution": result.source_resolution,
            "output_resolution": result.output_resolution,
            "duration_seconds": result.duration_seconds,
            "fps": result.fps,
            "library_item_id": item_id,
            "file_name": dest.name,
            "file_size_bytes": dest.stat().st_size,
            "container": "mp4",
            "created_at": now_iso(),
            "revision": 1,
        }
        self.storage.save_artifact(artifact)
        job["output_artifact_id"] = artifact_id
        job["library_item_id"] = item_id
        self.storage.save_job(job)
