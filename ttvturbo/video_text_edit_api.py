from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Query
from ttvturbo.api_utils import error_response
from ttvturbo.media_capabilities.storage import CapabilityNotFoundError, CapabilityStorageError
from ttvturbo.video_text_edit.schemas import (
    StartVideoTextEditRequest,
    VideoTextEditNotFoundError,
    VideoTextEditValidationError,
    VideoTextEditConflictError,
    VideoTextEditUnavailableError,
)


def build_video_text_edit_router(service) -> APIRouter:
    router = APIRouter(prefix="/api/video-text-edit", tags=["video-text-edit"])

    def mapped(exc: Exception):
        if isinstance(exc, CapabilityNotFoundError): return error_response(404, "video_edit_not_found", str(exc))
        if isinstance(exc, ValueError): return error_response(400, "video_edit_validation", str(exc))
        if isinstance(exc, CapabilityStorageError): return error_response(500, "video_edit_storage", str(exc))
        if isinstance(exc, RuntimeError): return error_response(409, "video_edit_conflict", str(exc))
        if isinstance(exc, VideoTextEditNotFoundError): return error_response(404, "video_edit_not_found", str(exc))
        if isinstance(exc, VideoTextEditValidationError): return error_response(400, "video_edit_validation", str(exc))
        if isinstance(exc, VideoTextEditConflictError): return error_response(409, "video_edit_conflict", str(exc))
        if isinstance(exc, VideoTextEditUnavailableError): return error_response(503, "video_edit_unavailable", str(exc))
        return error_response(500, "video_edit_internal", str(exc))

    @router.get("/status")
    def status(): return service.runtime_status()

    @router.get("/capabilities")
    def capabilities(): return service.capabilities()

    @router.post("/jobs", status_code=201)
    def start(request: StartVideoTextEditRequest):
        try: return service.start_job(**request.model_dump())
        except Exception as exc: return mapped(exc)

    @router.get("/jobs")
    def jobs(status: Optional[str] = Query(None)): return {"jobs": service.list_jobs(status_filter=status)}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str):
        try: return service.get_job(job_id)
        except Exception as exc: return mapped(exc)

    @router.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        try: return service.cancel_job(job_id)
        except Exception as exc: return mapped(exc)

    @router.post("/jobs/{job_id}/retry")
    def retry(job_id: str):
        try: return service.retry_job(job_id)
        except Exception as exc: return mapped(exc)

    @router.get("/artifacts")
    def artifacts(): return {"artifacts": service.list_artifacts()}

    @router.get("/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        try: return service.get_artifact(artifact_id)
        except Exception as exc: return mapped(exc)

    return router
