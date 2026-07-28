"""FastAPI integration for the Video Generation backend capability.

Endpoints (prefix ``/api``):

  GET  /api/video-generation/capabilities
  POST /api/video-generation/jobs
  GET  /api/video-generation/jobs
  GET  /api/video-generation/jobs/{id}
  POST /api/video-generation/jobs/{id}/cancel
  POST /api/video-generation/jobs/{id}/retry
  GET  /api/video-generation/artifacts
  GET  /api/video-generation/artifacts/{id}
  GET  /api/video-generation/status

Mirrors the structure of :mod:`visual_analysis_api`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ttvturbo.api_utils import error_response as _error_response

from ttvturbo.video_generation import (
    VideoGenerationConflictError,
    VideoGenerationNotFoundError,
    VideoGenerationService,
    VideoGenerationUnavailableError,
    VideoGenerationValidationError,
)

logger = logging.getLogger("ttvturbo.video_generation_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StartJobRequest(BaseModel):
    type: str = Field(description="TEXT_TO_VIDEO or IMAGE_TO_VIDEO")
    prompt: str
    source_image_asset_id: Optional[str] = None
    duration_seconds: float = 5.0
    aspect_ratio: str = "16:9"
    seed: Optional[int] = None
    options: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _map_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, VideoGenerationNotFoundError):
        return _error_response(404, "vg_not_found", str(exc))
    if isinstance(exc, VideoGenerationValidationError):
        return _error_response(400, "vg_validation", str(exc))
    if isinstance(exc, VideoGenerationConflictError):
        return _error_response(409, "vg_conflict", str(exc))
    if isinstance(exc, VideoGenerationUnavailableError):
        return _error_response(503, "vg_unavailable", str(exc))
    logger.exception("unexpected video-generation error")
    return _error_response(500, "vg_internal", "Internal video-generation error.")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_video_generation_router(
    service: VideoGenerationService,
) -> APIRouter:
    """Build the video-generation API router bound to a service instance."""
    router = APIRouter(prefix="/api", tags=["video-generation"])

    # ----------------------------------------------------------------- status
    @router.get("/video-generation/status")
    def status() -> JSONResponse:
        return JSONResponse(content=service.runtime_status())

    # ---------------------------------------------------------- capabilities
    @router.get("/video-generation/capabilities")
    def capabilities() -> JSONResponse:
        return JSONResponse(content=service.capabilities())

    # ----------------------------------------------------------------- jobs
    @router.post("/video-generation/jobs")
    def start_job(request: StartJobRequest) -> JSONResponse:
        try:
            job = service.start_job(
                generation_type=request.type,
                prompt=request.prompt,
                source_image_asset_id=request.source_image_asset_id,
                duration_seconds=request.duration_seconds,
                aspect_ratio=request.aspect_ratio,
                seed=request.seed,
                options=request.options,
            )
            return JSONResponse(status_code=201, content=job)
        except Exception as exc:
            return _map_error(exc)

    @router.get("/video-generation/jobs")
    def list_jobs(
        status_filter: Optional[str] = Query(default=None, alias="status"),
        type_filter: Optional[str] = Query(default=None, alias="type"),
    ) -> JSONResponse:
        jobs = service.list_jobs(status_filter=status_filter, generation_type=type_filter)
        return JSONResponse(content={"jobs": jobs})

    @router.get("/video-generation/jobs/{job_id}")
    def get_job(job_id: str) -> JSONResponse:
        try:
            job = service.get_job(job_id)
            return JSONResponse(content=job)
        except Exception as exc:
            return _map_error(exc)

    @router.post("/video-generation/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> JSONResponse:
        try:
            job = service.cancel_job(job_id)
            return JSONResponse(content=job)
        except Exception as exc:
            return _map_error(exc)

    @router.post("/video-generation/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> JSONResponse:
        try:
            job = service.retry_job(job_id)
            return JSONResponse(content=job)
        except Exception as exc:
            return _map_error(exc)

    # ----------------------------------------------------------------- artifacts
    @router.get("/video-generation/artifacts")
    def list_artifacts() -> JSONResponse:
        artifacts = service.list_artifacts()
        return JSONResponse(content={"artifacts": artifacts})

    @router.get("/video-generation/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> JSONResponse:
        try:
            artifact = service.get_artifact(artifact_id)
            return JSONResponse(content=artifact)
        except Exception as exc:
            return _map_error(exc)

    return router
