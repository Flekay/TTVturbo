"""Shared API helpers for TTVturbo FastAPI routers.

This module is the **single implementation** of the ``error_response``
helper that was previously duplicated (with slight variations) in
``voice_profiles_api.py``, ``vod_pipeline_api.py``,
``media_processing_api.py``, ``library_api.py`` and ``asr_api.py``.

All routers produce the same JSON error shape::

    {"detail": {"code": "<code>", "message": "<msg>", ...extra}}
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def error_response(
    status: int,
    code: str,
    message: str,
    **extra: Any,
) -> JSONResponse:
    """Build a JSON error response with a consistent ``detail`` shape.

    Parameters
    ----------
    status:
        HTTP status code.
    code:
        Machine-readable error code (e.g. ``"library_not_found"``).
    message:
        Human-readable error message.
    **extra:
        Additional fields merged into the ``detail`` object.
    """
    detail: dict[str, Any] = {"code": code, "message": message}
    detail.update(extra)
    return JSONResponse(status_code=status, content={"detail": detail})
