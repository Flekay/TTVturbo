"""FastAPI integration for the Ideas Research backend capability.

Endpoints (prefix ``/api``):

  Research runs:
    POST   /api/ideas/research-runs
    GET    /api/ideas/research-runs
    GET    /api/ideas/research-runs/{id}
    POST   /api/ideas/research-runs/{id}/cancel
    POST   /api/ideas/research-runs/{id}/retry

  Topics / sources:
    GET    /api/ideas/research-runs/{run_id}/sources
    GET    /api/ideas/topics
    GET    /api/ideas/topics/{id}

  Ideas:
    POST   /api/ideas/topics/{id}/ideas
    GET    /api/ideas
    GET    /api/ideas/{id}

  Scripts:
    POST   /api/ideas/{id}/scripts
    GET    /api/ideas/scripts
    GET    /api/ideas/scripts/{id}

  Status:
    GET    /api/ideas/status

Mirrors the structure of :mod:`visual_analysis_api`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ttvturbo.api_utils import error_response as _error_response

from ttvturbo.ideas_research import (
    IdeasResearchConflictError,
    IdeasResearchNotFoundError,
    IdeasResearchService,
    IdeasResearchUnavailableError,
    IdeasResearchValidationError,
)

logger = logging.getLogger("ttvturbo.ideas_research_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ResearchRunRequest(BaseModel):
    topics: list[str] = Field(min_length=1)
    language: str = "de"
    time_range: str = "7d"
    target_format: str = "SHORT"
    max_topics: int = Field(default=20, ge=1, le=200)


class CreateIdeaRequest(BaseModel):
    target_format: Optional[str] = None
    audience: Optional[str] = None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _map_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, IdeasResearchNotFoundError):
        return _error_response(404, "ideas_not_found", str(exc))
    if isinstance(exc, IdeasResearchValidationError):
        return _error_response(400, "ideas_validation", str(exc))
    if isinstance(exc, IdeasResearchConflictError):
        return _error_response(409, "ideas_conflict", str(exc))
    if isinstance(exc, IdeasResearchUnavailableError):
        return _error_response(503, "ideas_unavailable", str(exc))
    logger.exception("unexpected ideas-research error")
    return _error_response(500, "ideas_internal", "Internal ideas-research error.")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_ideas_research_router(service: IdeasResearchService) -> APIRouter:
    """Build the ideas-research API router bound to a service instance."""
    router = APIRouter(prefix="/api", tags=["ideas"])

    # ----------------------------------------------------------------- status
    @router.get("/ideas/status")
    def status() -> JSONResponse:
        return JSONResponse(content=service.runtime_status())

    # ----------------------------------------------------------------- runs
    @router.post("/ideas/research-runs")
    def start_run(request: ResearchRunRequest) -> JSONResponse:
        try:
            run = service.start_run(request.model_dump())
            return JSONResponse(status_code=201, content=run)
        except Exception as exc:
            return _map_error(exc)

    @router.get("/ideas/research-runs")
    def list_runs(
        status_filter: Optional[str] = Query(default=None, alias="status"),
    ) -> JSONResponse:
        runs = service.list_runs(status=status_filter)
        return JSONResponse(content={"runs": runs})

    @router.get("/ideas/research-runs/{run_id}")
    def get_run(run_id: str) -> JSONResponse:
        try:
            run = service.get_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_error(exc)

    @router.post("/ideas/research-runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> JSONResponse:
        try:
            run = service.cancel_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_error(exc)

    @router.post("/ideas/research-runs/{run_id}/retry")
    def retry_run(run_id: str) -> JSONResponse:
        try:
            run = service.retry_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_error(exc)

    # ----------------------------------------------------------------- sources
    @router.get("/ideas/research-runs/{run_id}/sources")
    def list_sources(run_id: str) -> JSONResponse:
        try:
            sources = service.list_sources(run_id)
            return JSONResponse(content={"sources": sources})
        except Exception as exc:
            return _map_error(exc)

    # ----------------------------------------------------------------- topics
    @router.get("/ideas/topics")
    def list_topics(
        run_id: Optional[str] = Query(default=None),
    ) -> JSONResponse:
        topics = service.list_topics(run_id=run_id)
        return JSONResponse(content={"topics": topics})

    @router.get("/ideas/topics/{topic_id}")
    def get_topic(topic_id: str) -> JSONResponse:
        try:
            topic = service.get_topic(topic_id)
            return JSONResponse(content=topic)
        except Exception as exc:
            return _map_error(exc)

    # ----------------------------------------------------------------- ideas
    @router.post("/ideas/topics/{topic_id}/ideas")
    def create_idea(topic_id: str, request: CreateIdeaRequest) -> JSONResponse:
        try:
            idea = service.create_idea(
                topic_id,
                request={"target_format": request.target_format, "audience": request.audience},
            )
            return JSONResponse(status_code=201, content=idea)
        except Exception as exc:
            return _map_error(exc)

    @router.get("/ideas")
    def list_ideas(
        run_id: Optional[str] = Query(default=None),
        topic_id: Optional[str] = Query(default=None),
    ) -> JSONResponse:
        ideas = service.list_ideas(run_id=run_id, topic_id=topic_id)
        return JSONResponse(content={"ideas": ideas})

    @router.get("/ideas/{idea_id}")
    def get_idea(idea_id: str) -> JSONResponse:
        try:
            idea = service.get_idea(idea_id)
            return JSONResponse(content=idea)
        except Exception as exc:
            return _map_error(exc)

    # ----------------------------------------------------------------- scripts
    @router.post("/ideas/{idea_id}/scripts")
    def create_script(idea_id: str) -> JSONResponse:
        try:
            script = service.create_script(idea_id)
            return JSONResponse(status_code=201, content=script)
        except Exception as exc:
            return _map_error(exc)

    @router.get("/ideas/scripts")
    def list_scripts(
        idea_id: Optional[str] = Query(default=None),
        run_id: Optional[str] = Query(default=None),
    ) -> JSONResponse:
        scripts = service.list_scripts(idea_id=idea_id, run_id=run_id)
        return JSONResponse(content={"scripts": scripts})

    @router.get("/ideas/scripts/{script_id}")
    def get_script(script_id: str) -> JSONResponse:
        try:
            script = service.get_script(script_id)
            return JSONResponse(content=script)
        except Exception as exc:
            return _map_error(exc)

    return router
