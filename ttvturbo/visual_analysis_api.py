"""FastAPI integration for the Visual Analysis backend capability.

Endpoints (prefix ``/api``):

  Visual analysis jobs:
    POST   /api/visual-analysis/jobs
    GET    /api/visual-analysis/jobs
    GET    /api/visual-analysis/jobs/{id}
    POST   /api/visual-analysis/jobs/{id}/cancel
    POST   /api/visual-analysis/jobs/{id}/retry

  Visual analysis artifacts:
    GET    /api/visual-analysis/artifacts/{id}
    GET    /api/visual-analysis/artifacts

  Layout templates:
    GET    /api/layout-templates
    POST   /api/layout-templates
    GET    /api/layout-templates/{id}
    PATCH  /api/layout-templates/{id}
    DELETE /api/layout-templates/{id}

Mirrors the structure of :mod:`conversation_mining_api`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ttvturbo.api_utils import error_response as _error_response

from ttvturbo.visual_analysis import (
    VisualAnalysisConflictError,
    VisualAnalysisNotFoundError,
    VisualAnalysisService,
    VisualAnalysisUnavailableError,
    VisualAnalysisValidationError,
)

logger = logging.getLogger("ttvturbo.visual_analysis_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StartJobRequest(BaseModel):
    media_item_id: str
    start_seconds: float = 0.0
    end_seconds: Optional[float] = None
    profile_id: Optional[str] = None
    force: bool = False
    manual_regions: Optional[list[dict[str, Any]]] = None


class CreateTemplateRequest(BaseModel):
    region_tracks: list[dict[str, Any]]
    twitch_profile_id: Optional[str] = None
    source_resolution: Optional[list[int]] = None
    name: Optional[str] = None
    confirmed: bool = False


class UpdateTemplateRequest(BaseModel):
    region_tracks: Optional[list[dict[str, Any]]] = None
    twitch_profile_id: Optional[str] = None
    source_resolution: Optional[list[int]] = None
    name: Optional[str] = None
    confirmed: Optional[bool] = None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _map_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, VisualAnalysisNotFoundError):
        return _error_response(404, "va_not_found", str(exc))
    if isinstance(exc, VisualAnalysisValidationError):
        return _error_response(400, "va_validation", str(exc))
    if isinstance(exc, VisualAnalysisConflictError):
        return _error_response(409, "va_conflict", str(exc))
    if isinstance(exc, VisualAnalysisUnavailableError):
        return _error_response(503, "va_unavailable", str(exc))
    logger.exception("unexpected visual-analysis error")
    return _error_response(500, "va_internal", "Internal visual-analysis error.")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_visual_analysis_router(
    service: VisualAnalysisService,
) -> APIRouter:
    """Build the visual-analysis API router bound to a service instance."""
    router = APIRouter(prefix="/api", tags=["visual-analysis"])

    # ----------------------------------------------------------------- status
    @router.get("/visual-analysis/status")
    def status() -> JSONResponse:
        return JSONResponse(content=service.runtime_status())

    # ----------------------------------------------------------------- jobs
    @router.post("/visual-analysis/jobs")
    def start_job(request: StartJobRequest) -> JSONResponse:
        try:
            job = service.start_job(
                media_item_id=request.media_item_id,
                start_seconds=request.start_seconds,
                end_seconds=request.end_seconds,
                profile_id=request.profile_id,
                force=request.force,
                manual_regions=request.manual_regions,
            )
            return JSONResponse(status_code=201, content=job)
        except Exception as exc:
            return _map_error(exc)

    @router.get("/visual-analysis/jobs")
    def list_jobs(
        media_item_id: Optional[str] = Query(default=None),
        status_filter: Optional[str] = Query(default=None, alias="status"),
    ) -> JSONResponse:
        jobs = service.list_jobs(media_item_id=media_item_id, status=status_filter)
        return JSONResponse(content={"jobs": jobs})

    @router.get("/visual-analysis/jobs/{job_id}")
    def get_job(job_id: str) -> JSONResponse:
        try:
            job = service.get_job(job_id)
            return JSONResponse(content=job)
        except Exception as exc:
            return _map_error(exc)

    @router.post("/visual-analysis/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> JSONResponse:
        try:
            job = service.cancel_job(job_id)
            return JSONResponse(content=job)
        except Exception as exc:
            return _map_error(exc)

    @router.post("/visual-analysis/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> JSONResponse:
        try:
            job = service.retry_job(job_id)
            return JSONResponse(content=job)
        except Exception as exc:
            return _map_error(exc)

    # ----------------------------------------------------------------- artifacts
    @router.get("/visual-analysis/artifacts")
    def list_artifacts(
        media_item_id: Optional[str] = Query(default=None),
    ) -> JSONResponse:
        artifacts = service.list_artifacts(media_item_id=media_item_id)
        return JSONResponse(content={"artifacts": artifacts})

    @router.get("/visual-analysis/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> JSONResponse:
        try:
            artifact = service.get_artifact(artifact_id)
            return JSONResponse(content=artifact)
        except Exception as exc:
            return _map_error(exc)

    # ----------------------------------------------------------------- templates
    @router.get("/layout-templates")
    def list_templates(
        twitch_profile_id: Optional[str] = Query(default=None),
        width: Optional[int] = Query(default=None),
        height: Optional[int] = Query(default=None),
    ) -> JSONResponse:
        resolution: Optional[list[int]] = None
        if width is not None and height is not None:
            resolution = [width, height]
        templates = service.list_templates(
            twitch_profile_id=twitch_profile_id,
            source_resolution=resolution,
        )
        return JSONResponse(content={"templates": templates})

    @router.post("/layout-templates")
    def create_template(request: CreateTemplateRequest) -> JSONResponse:
        try:
            template = service.create_template(
                region_tracks=request.region_tracks,
                twitch_profile_id=request.twitch_profile_id,
                source_resolution=request.source_resolution,
                name=request.name,
                confirmed=request.confirmed,
            )
            return JSONResponse(status_code=201, content=template)
        except Exception as exc:
            return _map_error(exc)

    @router.get("/layout-templates/{template_id}")
    def get_template(template_id: str) -> JSONResponse:
        try:
            template = service.get_template(template_id)
            return JSONResponse(content=template)
        except Exception as exc:
            return _map_error(exc)

    @router.patch("/layout-templates/{template_id}")
    def update_template(template_id: str, request: UpdateTemplateRequest) -> JSONResponse:
        try:
            template = service.update_template(
                template_id,
                region_tracks=request.region_tracks,
                twitch_profile_id=request.twitch_profile_id,
                source_resolution=request.source_resolution,
                name=request.name,
                confirmed=request.confirmed,
            )
            return JSONResponse(content=template)
        except Exception as exc:
            return _map_error(exc)

    @router.delete("/layout-templates/{template_id}")
    def delete_template(template_id: str) -> JSONResponse:
        try:
            deleted = service.delete_template(template_id)
            if not deleted:
                return _error_response(404, "va_not_found", f"template not found: {template_id}")
            return JSONResponse(content={"id": template_id, "deleted": True})
        except Exception as exc:
            return _map_error(exc)

    return router
