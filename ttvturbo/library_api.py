"""FastAPI router for the library — the persistent video store.

Endpoints:
  GET    /api/library/items            — list all library items
  GET    /api/library/items/{item_id}  — get a single item
  GET    /api/library/items/{item_id}/file — download the video file
  POST   /api/library/uploads          — upload a file (creates a library item)
  DELETE /api/library/items/{item_id}  — delete an item (file + metadata)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ttvturbo.api_utils import error_response as _error_response

from ttvturbo.library import (
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
    LibraryService,
    LibraryStorageError,
    LibraryValidationError,
)

logger = logging.getLogger("ttvturbo.library_api")


# _error_response is imported from ttvturbo.api_utils.


def _map_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, LibraryNotFoundError):
        return _error_response(404, "library_not_found", str(exc))
    if isinstance(exc, LibraryConflictError):
        return _error_response(409, "library_conflict", str(exc))
    if isinstance(exc, LibraryValidationError):
        return _error_response(400, "library_validation", str(exc))
    if isinstance(exc, LibraryStorageError):
        return _error_response(500, "library_storage", str(exc))
    return _error_response(500, "library_error", str(exc))


def build_library_router(service: LibraryService) -> APIRouter:
    """Build the library API router."""
    router = APIRouter(prefix="/api/library", tags=["library"])

    @router.get("/items")
    def list_items() -> JSONResponse:
        try:
            items = service.list_items()
        except Exception as exc:
            return _map_error(exc)
        return JSONResponse(content={"items": items})

    @router.get("/items/{item_id}")
    def get_item(item_id: str) -> JSONResponse:
        try:
            item = service.get_item(item_id)
        except Exception as exc:
            return _map_error(exc)
        return JSONResponse(content=item)

    @router.get("/items/{item_id}/file")
    def download_item_file(item_id: str):
        try:
            path = service.item_file_path(item_id)
        except LibraryNotFoundError as exc:
            return _error_response(404, "library_not_found", str(exc))
        except Exception as exc:
            return _map_error(exc)
        return FileResponse(path, filename=path.name)

    @router.post("/uploads")
    async def upload_to_library(file: UploadFile) -> JSONResponse:
        """Upload a media file to the library.

        The file is streamed to a temp path and atomically renamed, so a
        partial upload never leaves a half-written file at the final path.
        """
        if not file.filename:
            return _error_response(400, "upload_validation", "File name is required.")
        item_id = None
        try:
            meta = service.create_upload_item(file_name=file.filename, title=file.filename)
            item_id = meta["id"]
            # Stream the file into the item directory atomically.
            dest = await service.storage.stream_item_file(item_id, file.filename, file)
            meta["file_size_bytes"] = dest.stat().st_size
            service.storage.save_item(meta)
        except Exception as exc:
            if item_id is not None:
                try:
                    service.delete_item(item_id)
                except Exception:
                    pass
            return _map_error(exc)
        finally:
            await file.close()
        return JSONResponse(status_code=201, content=meta)

    @router.delete("/items/{item_id}")
    def delete_item(item_id: str) -> JSONResponse:
        try:
            deleted = service.delete_item(item_id)
        except Exception as exc:
            return _map_error(exc)
        if not deleted:
            return _error_response(404, "library_not_found", "Item not found.")
        return JSONResponse(content={"deleted": True, "id": item_id})

    return router
