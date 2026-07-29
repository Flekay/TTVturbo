"""FastAPI integration layer for the natural-language editor command parser.

Endpoints (prefix ``/api``):

  GET  /api/editor-command/status
  POST /api/editor-command/parse

The parser runs the local text LLM (the same model Conversation Mining
uses) in a one-shot worker subprocess and returns a structured intent
JSON that the frontend applies through the existing editor operations.
The service never commits operations itself.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ttvturbo.api_utils import error_response as _error_response

from ttvturbo.media_processing import (
    EditorCommandError,
    EditorCommandService,
    EditorCommandTimeoutError,
    EditorCommandUnavailableError,
    EditorCommandValidationError,
    EditorCommandWorkerError,
)

logger = logging.getLogger("ttvturbo.editor_command_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ParseRequest(BaseModel):
    command: str
    context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _map_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, EditorCommandValidationError):
        return _error_response(400, "editor_command_validation", str(exc))
    if isinstance(exc, EditorCommandUnavailableError):
        return _error_response(503, "editor_command_unavailable", str(exc))
    if isinstance(exc, EditorCommandTimeoutError):
        return _error_response(504, "editor_command_timeout", str(exc))
    if isinstance(exc, EditorCommandWorkerError):
        return _error_response(502, "editor_command_worker", str(exc))
    if isinstance(exc, EditorCommandError):
        return _error_response(500, "editor_command_error", str(exc))
    logger.exception("unexpected editor-command error")
    return _error_response(500, "editor_command_internal", "Internal editor-command error.")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_editor_command_router(service: EditorCommandService) -> APIRouter:
    """Build the editor-command API router bound to a service instance."""
    router = APIRouter(prefix="/api", tags=["editor-command"])

    @router.get("/editor-command/status")
    def editor_command_status() -> JSONResponse:
        return JSONResponse(content=service.runtime_status())

    @router.post("/editor-command/parse")
    def parse_editor_command(req: ParseRequest) -> JSONResponse:
        try:
            intent = service.parse(req.command, req.context)
            return JSONResponse(content={"intent": intent})
        except Exception as exc:
            return _map_error(exc)

    return router
