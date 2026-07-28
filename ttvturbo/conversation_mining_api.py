"""FastAPI integration layer for Conversation Mining.

Endpoints (prefix ``/api``):

  GET    /api/conversation-mining/status
  POST   /api/conversation-mining/runs
  GET    /api/conversation-mining/runs
  GET    /api/conversation-mining/runs/{run_id}
  POST   /api/conversation-mining/runs/{run_id}/cancel
  POST   /api/conversation-mining/runs/{run_id}/retry
  DELETE /api/conversation-mining/runs/{run_id}

Mirrors the structure of :mod:`media_processing_api`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ttvturbo.api_utils import error_response as _error_response

from ttvturbo.media_processing import (
    ConversationMiningConflictError,
    ConversationMiningNotFoundError,
    ConversationMiningService,
    ConversationMiningUnavailableError,
    ConversationMiningValidationError,
)

logger = logging.getLogger("ttvturbo.conversation_mining_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StartMiningRunRequest(BaseModel):
    media_item_id: str
    force: bool = False


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _map_mining_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, ConversationMiningNotFoundError):
        return _error_response(404, "mining_run_not_found", str(exc))
    if isinstance(exc, ConversationMiningValidationError):
        return _error_response(400, "mining_run_validation", str(exc))
    if isinstance(exc, ConversationMiningConflictError):
        return _error_response(409, "mining_run_conflict", str(exc))
    if isinstance(exc, ConversationMiningUnavailableError):
        return _error_response(503, "mining_unavailable", str(exc))
    logger.exception("unexpected conversation-mining error")
    return _error_response(500, "mining_internal", "Internal conversation-mining error.")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_conversation_mining_router(
    mining_service: ConversationMiningService,
) -> APIRouter:
    """Build the conversation-mining API router bound to a service instance."""
    router = APIRouter(prefix="/api", tags=["conversation-mining"])

    @router.get("/conversation-mining/status")
    def mining_status() -> JSONResponse:
        status = mining_service.runtime_status()
        return JSONResponse(content=status)

    @router.post("/conversation-mining/runs")
    def start_mining_run(request: StartMiningRunRequest) -> JSONResponse:
        try:
            run = mining_service.start_run(
                media_item_id=request.media_item_id,
                force=request.force,
            )
            return JSONResponse(status_code=201, content=run)
        except Exception as exc:
            return _map_mining_error(exc)

    @router.get("/conversation-mining/runs")
    def list_mining_runs(
        media_item_id: Optional[str] = Query(default=None),
        transcript_id: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        stale: Optional[bool] = Query(default=None),
    ) -> JSONResponse:
        runs = mining_service.list_runs(
            media_item_id=media_item_id,
            transcript_id=transcript_id,
            status=status,
            stale=stale,
        )
        return JSONResponse(content={"runs": runs})

    @router.get("/conversation-mining/runs/{run_id}")
    def get_mining_run(run_id: str) -> JSONResponse:
        try:
            run = mining_service.get_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_mining_error(exc)

    @router.post("/conversation-mining/runs/{run_id}/cancel")
    def cancel_mining_run(run_id: str) -> JSONResponse:
        try:
            run = mining_service.cancel_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_mining_error(exc)

    @router.post("/conversation-mining/runs/{run_id}/retry")
    def retry_mining_run(run_id: str) -> JSONResponse:
        try:
            run = mining_service.retry_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_mining_error(exc)

    @router.delete("/conversation-mining/runs/{run_id}")
    def delete_mining_run(run_id: str) -> JSONResponse:
        try:
            mining_service.delete_run(run_id)
            return JSONResponse(content={"id": run_id, "deleted": True})
        except Exception as exc:
            return _map_mining_error(exc)

    return router
