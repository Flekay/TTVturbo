"""Preview/final render service for immutable EditProject projections."""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from ttvturbo.media_capabilities import SubprocessCapabilityService, now_iso, register_derived_library_item, resolve_library_media, sha256_file
from ttvturbo.settings import Settings

from .schemas import (
    RenderMode,
    RenderResult,
    StartRenderRequest,
    RenderingConflictError,
    RenderingNotFoundError,
    RenderingUnavailableError,
    RenderingValidationError,
)
from .storage import RenderingStorage

OPERATION = "rendering"


class RenderingService(SubprocessCapabilityService):
    def __init__(
        self,
        *,
        storage: RenderingStorage,
        edit_project_service: Any,
        library_service: Any,
        settings: Settings,
        worker_python: Optional[str] = None,
        ffmpeg_path: Optional[str] = None,
        ffprobe_path: Optional[str] = None,
        worker_runner=None,
    ) -> None:
        self.edit_project_service = edit_project_service
        self.library_service = library_service
        self.settings = settings
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe") or "ffprobe"
        self._runtime_cache: tuple[float, dict[str, Any]] | None = None
        super().__init__(
            storage=storage,
            operation=OPERATION,
            worker_module="ttvturbo.rendering.worker",
            worker_python=worker_python,
            max_concurrent=settings.rendering_max_concurrent,
            worker_runner=worker_runner,
        )

    def runtime_status(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._runtime_cache and now - self._runtime_cache[0] < 10:
            return dict(self._runtime_cache[1])
        ffmpeg_ok = bool(shutil.which(self.ffmpeg_path) or Path(self.ffmpeg_path).is_file())
        ffprobe_ok = bool(shutil.which(self.ffprobe_path) or Path(self.ffprobe_path).is_file())
        reasons = []
        if not ffmpeg_ok: reasons.append("ffmpeg unavailable")
        if not ffprobe_ok: reasons.append("ffprobe unavailable")
        result = {
            "available": ffmpeg_ok and ffprobe_ok,
            "configured": True,
            "busy": any(j.get("status") in {"QUEUED", "RUNNING"} for j in self.storage.iter_jobs()),
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
            "modes": ["PREVIEW", "FINAL"],
            "video_codecs": ["libx264", "libx265", "h264_nvenc", "hevc_nvenc"],
            "audio_codecs": ["aac", "libopus"],
            "supported_tracks": ["UNIVERSAL", "VIDEO", "AUDIO", "GAMEPLAY", "FACECAM", "CAPTIONS", "OVERLAY"],
            "supported_elements": ["VIDEO", "AUDIO", "IMAGE", "TEXT"],
            "supported_effects": ["FADE"],
            "supports_alpha_inputs": True,
            "supports_historical_commits": True,
            "reasons": status["reasons"],
        }

    @staticmethod
    def _preview_size(width: int, height: int, max_dimension: int) -> tuple[int, int]:
        factor = min(1.0, float(max_dimension) / max(width, height))
        w = max(2, int(round(width * factor / 2)) * 2)
        h = max(2, int(round(height * factor / 2)) * 2)
        return w, h

    def start_job(self, **payload: Any) -> dict[str, Any]:
        try:
            request = StartRenderRequest.model_validate(payload)
        except ValidationError as exc:
            raise RenderingValidationError(str(exc)) from exc
        try:
            base_projection = self.edit_project_service.render_projection(
                request.project_id,
                sequence_id=request.sequence_id,
                commit_id=request.commit_id,
                render_settings=None,
            )
        except Exception as exc:
            raise RenderingNotFoundError(str(exc)) from exc
        output = base_projection["output_settings"]
        settings = request.settings.model_dump(mode="json")
        if request.settings.mode == RenderMode.PREVIEW:
            width, height = self._preview_size(int(output["width"]), int(output["height"]), request.settings.preview_max_dimension)
        else:
            width, height = int(output["width"]), int(output["height"])
        projection_settings = {**settings, "width": width, "height": height}
        try:
            projection = self.edit_project_service.render_projection(
                request.project_id,
                sequence_id=request.sequence_id,
                commit_id=base_projection["commit_id"],
                render_settings=projection_settings,
            )
        except Exception as exc:
            raise RenderingValidationError(str(exc)) from exc

        source_files: dict[str, dict[str, Any]] = {}
        for source_ref in projection.get("source_references") or []:
            media_id = source_ref["media_item_id"]
            try:
                library_meta, path = resolve_library_media(self.library_service, media_id, source_ref.get("asset_id"))
            except Exception as exc:
                raise RenderingNotFoundError(str(exc)) from exc
            actual_hash = sha256_file(path)
            if actual_hash != source_ref["sha256"]:
                raise RenderingConflictError(f"source changed since project import: {media_id}")
            from ttvturbo.media_capabilities.utils import media_metadata
            metadata = media_metadata(self.ffprobe_path, path)
            source_files[media_id] = {
                "path": str(path),
                "sha256": actual_hash,
                "file_type": library_meta.get("file_type"),
                **metadata,
            }

        # Idempotent cache: an exact projection+settings already rendered in
        # the requested mode can be reused without another encode.
        for artifact in self.storage.iter_artifacts():
            artifact_lifecycle = artifact.get("lifecycle", "PERSISTENT")
            if (
                artifact.get("projection_hash") == projection["projection_hash"]
                and artifact.get("mode") == request.settings.mode.value
                and artifact_lifecycle == request.output_lifecycle
            ):
                try:
                    self.library_service.get_item(artifact["library_item_id"])
                    cached_job = {
                        "schema_version": 1,
                        "id": str(uuid.uuid4()),
                        "operation": OPERATION,
                        "status": "COMPLETED",
                        "progress": 100.0,
                        "current_stage": None,
                        "created_at": now_iso(),
                        "started_at": now_iso(),
                        "completed_at": now_iso(),
                        "project_id": request.project_id,
                        "sequence_id": request.sequence_id,
                        "commit_id": projection["commit_id"],
                        "projection_hash": projection["projection_hash"],
                        "state_hash": projection["state_hash"],
                        "settings": settings,
                        "output_lifecycle": request.output_lifecycle,
                        "output_artifact_id": artifact["id"],
                        "library_item_id": artifact["library_item_id"],
                        "cached": True,
                        "attempt": 1,
                        "error": None,
                    }
                    self.storage.save_job(cached_job)
                    return cached_job
                except Exception:
                    pass

        job_id = str(uuid.uuid4())
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
            "project_id": request.project_id,
            "sequence_id": request.sequence_id,
            "commit_id": projection["commit_id"],
            "projection_hash": projection["projection_hash"],
            "state_hash": projection["state_hash"],
            "settings": settings,
            "output_lifecycle": request.output_lifecycle,
            "attempt": 1,
            "output_artifact_id": None,
            "library_item_id": None,
            "cached": False,
            "error": None,
        }
        worker_job = {
            "job_id": job_id,
            "projection": projection,
            "source_files": source_files,
            "settings": settings,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
        }
        try:
            return self._start_prepared_job(job, worker_job)
        except RuntimeError as exc:
            if "unavailable" in str(exc): raise RenderingUnavailableError(str(exc)) from exc
            raise RenderingConflictError(str(exc)) from exc

    def _finalize_job(self, job_id: str) -> None:
        job = self.storage.load_job(job_id)
        if job.get("status") != "COMPLETED" or job.get("output_artifact_id"):
            return
        payload = self.storage.load_result(job_id)
        if not payload or not payload.get("success"):
            raise RuntimeError("render result is missing")
        result = RenderResult.model_validate(payload)
        output = self.storage.job_dir(job_id) / result.output_file
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("render output is missing")
        artifact_id = str(uuid.uuid4())
        mode = job["settings"]["mode"]
        # The first project source is the lineage root. The artifact itself
        # remains tied to exact project/sequence/commit/state hashes.
        worker = self.storage.load_worker_job(job_id)
        refs = worker["projection"].get("source_references") or []
        source_media_id = refs[0]["media_item_id"] if refs else ""
        item_id, dest = register_derived_library_item(
            self.library_service,
            output_path=output,
            title=f"{'Preview' if mode == 'PREVIEW' else 'Render'} – {job['project_id']} / {job['sequence_id']}",
            duration_seconds=result.duration_seconds,
            operation=OPERATION,
            source_media_item_id=source_media_id,
            artifact_id=artifact_id,
            container="mp4",
            metadata={
                "job_id": job_id,
                "project_id": job["project_id"],
                "sequence_id": job["sequence_id"],
                "commit_id": job["commit_id"],
                "projection_hash": result.projection_hash,
                "mode": mode,
            },
            lifecycle=job.get("output_lifecycle", "PERSISTENT"),
        )
        artifact = {
            "schema_version": 1,
            "id": artifact_id,
            "job_id": job_id,
            "artifact_type": "EDIT_PREVIEW" if mode == "PREVIEW" else "EDIT_RENDER",
            "mode": mode,
            "project_id": job["project_id"],
            "sequence_id": job["sequence_id"],
            "commit_id": job["commit_id"],
            "state_hash": result.state_hash,
            "projection_hash": result.projection_hash,
            "settings": job["settings"],
            "width": result.width,
            "height": result.height,
            "fps": result.fps,
            "duration_seconds": result.duration_seconds,
            "video_codec": result.video_codec,
            "audio_codec": result.audio_codec,
            "library_item_id": item_id,
            "file_name": dest.name,
            "file_size_bytes": dest.stat().st_size,
            "container": "mp4",
            "lifecycle": job.get("output_lifecycle", "PERSISTENT"),
            "created_at": now_iso(),
            "revision": 1,
        }
        self.storage.save_artifact(artifact)
        job["output_artifact_id"] = artifact_id
        job["library_item_id"] = item_id
        self.storage.save_job(job)
