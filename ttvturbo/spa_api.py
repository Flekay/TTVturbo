"""SPA (Single Page Application) router — ``/`` and ``/{full_path:path}``.

Extracted from ``app_factory.py`` so the factory stays a thin wiring
layer.  Serves the built React frontend from ``settings.frontend_dist``.

The catch-all ``/{full_path:path}`` route must be registered **after**
all API routes so it does not shadow them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def build_spa_router(container: Any) -> APIRouter:
    """Build the SPA router (index + static assets + catch-all).

    *container* must expose ``settings`` (with ``frontend_dist``).
    """
    router = APIRouter(tags=["spa"], include_in_schema=False)

    def _frontend_dist() -> Path:
        assert container.settings is not None
        return container.settings.frontend_dist

    def _spa_index() -> FileResponse:
        index_html = _frontend_dist() / "index.html"
        if not index_html.is_file():
            raise HTTPException(
                status_code=404,
                detail="frontend not built. Run `npm --prefix frontend run build`.",
            )
        return FileResponse(index_html, media_type="text/html")

    @router.get("/")
    def index() -> FileResponse:
        if (_frontend_dist() / "index.html").is_file():
            return _spa_index()
        raise HTTPException(
            status_code=404,
            detail="frontend/dist/index.html not found - build the React frontend first",
        )

    @router.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")

        dist = _frontend_dist()
        if full_path:
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="Not found.")
            if candidate.is_file():
                return FileResponse(candidate)

        return _spa_index()

    return router
