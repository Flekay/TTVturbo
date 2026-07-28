"""Regression tests for the app factory, settings, and test isolation.

Verifies:
1. Importing the app module creates no files in the repository data/ dir.
2. Two apps with different data roots are fully isolated.
3. The app lifespan initialises only the provided data root.
4. Tests can replace services via ServiceOverrides.
5. The production app is available via the existing start path (app.app).
6. The API route snapshot is unchanged or deviations are documented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app_factory import ServiceContainer, ServiceOverrides, create_app, find_executable
from settings import APP_NAME, APP_VERSION, Settings


# ---------------------------------------------------------------------------
# 1. Import side effects — no files in repository data/
# ---------------------------------------------------------------------------


def test_import_app_creates_no_files_in_repo_data(tmp_path, monkeypatch):
    """Importing app with a temp TTVTURBO_DATA_DIR must not touch ./data/."""
    repo_data = Path(__file__).resolve().parent.parent / "data"
    snapshot_before: set[str] = set()
    if repo_data.is_dir():
        snapshot_before = {
            str(p.relative_to(repo_data))
            for p in repo_data.rglob("*")
            if p.is_file()
        }

    # Point TTVTURBO_DATA_DIR at a temp dir so the production app uses it.
    monkeypatch.setenv("TTVTURBO_DATA_DIR", str(tmp_path / "import_test_data"))
    import importlib
    import app as app_module
    importlib.reload(app_module)

    snapshot_after: set[str] = set()
    if repo_data.is_dir():
        snapshot_after = {
            str(p.relative_to(repo_data))
            for p in repo_data.rglob("*")
            if p.is_file()
        }

    # No new files should have appeared in the real data/ directory.
    new_files = snapshot_after - snapshot_before
    assert not new_files, f"Import created unexpected files in data/: {new_files}"


def test_import_app_factory_has_no_side_effects():
    """Importing app_factory must not create directories or recover jobs."""
    import app_factory
    # The module is already imported; just verify it doesn't have
    # module-level service instances or directories.
    assert not hasattr(app_factory, "app")
    assert not hasattr(app_factory, "DATA_DIR")
    assert not hasattr(app_factory, "library_service")


# ---------------------------------------------------------------------------
# 2. Two apps with different data roots are isolated
# ---------------------------------------------------------------------------


def test_two_apps_with_different_data_roots_are_isolated(tmp_path):
    """Two apps created with different settings must not share state."""
    settings_a = Settings(data_root=tmp_path / "app_a")
    settings_b = Settings(data_root=tmp_path / "app_b")

    app_a = create_app(settings=settings_a)
    app_b = create_app(settings=settings_b)

    assert app_a.state.container is not app_b.state.container
    assert app_a.state.settings.data_root != app_b.state.settings.data_root

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        # Each app's recordings dir is different.
        rec_a = app_a.state.container.paths.recordings
        rec_b = app_b.state.container.paths.recordings
        assert rec_a != rec_b
        assert rec_a.parent == tmp_path / "app_a"
        assert rec_b.parent == tmp_path / "app_b"

        # Status endpoints work independently.
        resp_a = client_a.get("/api/status")
        resp_b = client_b.get("/api/status")
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200


# ---------------------------------------------------------------------------
# 3. Lifespan initialises only the provided data root
# ---------------------------------------------------------------------------


def test_lifespan_initialises_only_provided_data_root(tmp_path):
    """The lifespan must create directories only under the provided data root."""
    data_root = tmp_path / "lifespan_test"
    settings = Settings(data_root=data_root)
    assert not data_root.exists()

    app = create_app(settings=settings)
    # Before lifespan, the data root should not exist.
    assert not data_root.exists()

    with TestClient(app):
        # After lifespan, the data root and subdirectories exist.
        assert data_root.is_dir()
        assert (data_root / "recordings").is_dir()
        assert (data_root / "library").is_dir()

    # The container has the correct paths.
    assert app.state.container.paths.data_root == data_root


# ---------------------------------------------------------------------------
# 4. ServiceOverrides can replace services
# ---------------------------------------------------------------------------


class _FakeVoiceCloneService:
    """Minimal fake for VoiceCloneService."""

    def status(self) -> dict:
        return {"available": True, "busy": False, "device": "fake"}

    def set_profile_reference_resolver(self, resolver) -> None:
        pass


def test_service_overrides_replace_voice_clone(tmp_path):
    """ServiceOverrides must let tests inject fakes without touching disk."""
    settings = Settings(data_root=tmp_path / "override_test")
    overrides = ServiceOverrides(voice_clone_service=_FakeVoiceCloneService())
    app = create_app(settings=settings, overrides=overrides)

    with TestClient(app) as client:
        resp = client.get("/api/voice-clone/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["device"] == "fake"


# ---------------------------------------------------------------------------
# 5. Production app available via existing start path
# ---------------------------------------------------------------------------


def test_production_app_available_via_app_module():
    """app.app must be a FastAPI instance for uvicorn app:app."""
    import app as app_module

    assert app_module.app is not None
    assert app_module.app.title == APP_NAME


def test_app_module_exports_find_executable():
    """app._find_executable must still be importable (backward compat)."""
    import app as app_module

    assert callable(app_module._find_executable)
    # It should find something on PATH (e.g., python itself is not searched,
    # but the function should not crash).
    result = app_module._find_executable("nonexistent_xyz_12345")
    assert result is None


# ---------------------------------------------------------------------------
# 6. API route snapshot comparison
# ---------------------------------------------------------------------------


def _collect_routes(app) -> list[dict]:
    """Collect all registered routes from a FastAPI app."""
    routes: list[dict] = []

    def _walk(route_list) -> None:
        for route in route_list:
            # FastAPI wraps included routers in _IncludedRouter objects.
            if hasattr(route, "original_router"):
                _walk(route.original_router.routes)
                continue
            if hasattr(route, "routes"):
                _walk(route.routes)
                continue
            if hasattr(route, "methods") and hasattr(route, "path"):
                for method in sorted(route.methods or []):
                    if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
                        routes.append({"method": method, "path": route.path})

    _walk(app.routes)
    routes.sort(key=lambda r: (r["path"], r["method"]))
    return routes


def test_api_route_snapshot_unchanged(tmp_path):
    """The set of public API routes must match the baseline snapshot.

    Routes listed in ``INTENTIONALLY_REMOVED`` were removed as part of the
    cleanup and are excluded from the comparison.  The baseline snapshot
    itself (``api_routes_before.json``) is immutable.
    """
    snapshot_path = Path(__file__).parent / "contracts" / "api_routes_before.json"
    with open(snapshot_path, encoding="utf-8") as fh:
        baseline = json.load(fh)

    # Routes intentionally removed during cleanup (Auftrag 5).
    # Legacy upload endpoints in media_processing_api.py — superseded by
    # library_api.py which provides /api/library/items and POST /api/library/uploads.
    INTENTIONALLY_REMOVED = {
        ("GET", "/api/library/uploads"),
        ("GET", "/api/library/uploads/{upload_id}/file"),
        ("DELETE", "/api/library/uploads/{upload_id}"),
    }

    settings = Settings(data_root=tmp_path / "route_check")
    app = create_app(settings=settings)
    actual_routes = _collect_routes(app)

    baseline_set = {(r["method"], r["path"]) for r in baseline["routes"]}
    # Filter out FastAPI built-in routes (openapi, docs, redoc).
    _builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    actual_set = {
        (r["method"], r["path"])
        for r in actual_routes
        if r["path"] not in _builtin
    }

    missing = (baseline_set - actual_set) - INTENTIONALLY_REMOVED
    added = actual_set - baseline_set

    assert not missing, f"Routes missing from app: {sorted(missing)}"
    # Added routes are OK as long as they are additive (no existing route removed).
    # We log them but don't fail.
    if added:
        pytest.skip(f"New routes added (additive, acceptable): {sorted(added)}")
