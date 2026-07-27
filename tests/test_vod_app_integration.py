"""Integration tests for the VOD-pipeline endpoints on the real app.

These hit the real ``app:app`` FastAPI instance (the same one uvicorn
serves), so they verify the router wiring, the status payload shape and
that no secret leaks through the public surface.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Point the data + download dirs at a temp location so the tests never
    # touch the developer's real ttvturbo_data directory.
    monkeypatch.setenv("TTVTURBO_DATA_DIR", str(tmp_path / "ttvturbo_data"))
    monkeypatch.setenv("TTVTURBO_VOD_DOWNLOAD_DIR", str(tmp_path / "vods"))
    # Force a fresh app import so the env vars take effect.
    import importlib
    import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_status_payload_includes_vod_pipeline(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "vod_pipeline" in data
    assert data["vod_pipeline"]["profiles"] == 0
    assert data["vod_pipeline"]["vods"] == 0
    assert "features" in data
    assert "vod_pipeline" in data["features"]
    assert "twitch_profiles" in data["features"]


def test_twitch_status_endpoint_wired(client):
    resp = client.get("/api/twitch/status")
    assert resp.status_code == 200
    data = resp.json()
    # yt-dlp + ffprobe are available in the test env -> available is True.
    assert "available" in data
    assert "reasons" in data
    assert "downloader_available" in data
    # No credential fields exist anymore.
    assert "client_id_configured" not in data
    assert "client_secret_configured" not in data


def test_vod_pipeline_endpoints_wired(client):
    # Empty store -> empty list, not 404.
    resp = client.get("/api/twitch/profiles")
    assert resp.status_code == 200
    assert resp.json() == {"profiles": []}
    resp = client.get("/api/vods")
    assert resp.status_code == 200
    assert resp.json() == {"vods": []}


def test_no_secret_in_status_response(client):
    resp = client.get("/api/status")
    # No credentials are configured anymore; nothing secret should appear.
    assert "client_secret" not in resp.text
    assert "fake-token" not in resp.text
