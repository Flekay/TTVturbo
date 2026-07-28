"""Tests for static frontend serving and SPA fallback."""

from __future__ import annotations

from pathlib import Path


def test_index_returns_404_when_dist_missing(client, monkeypatch, tmp_path):
    # Point frontend_dist at an empty tmp dir so the built frontend is "missing".
    container = client.app.state.container
    monkeypatch.setattr(container.settings, "frontend_dist", tmp_path / "dist")
    resp = client.get("/")
    # No legacy fallback: a clear 404 tells the developer to build the frontend.
    assert resp.status_code == 404
    assert "frontend" in resp.text.lower()


def test_index_serves_built_frontend_when_dist_present(client, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><head><title>TTVturbo React</title></head>"
        "<body><div id=\"root\"></div></body></html>",
        encoding="utf-8",
    )
    container = client.app.state.container
    monkeypatch.setattr(container.settings, "frontend_dist", dist)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "TTVturbo React" in resp.text
    assert "id=\"root\"" in resp.text


def test_spa_fallback_serves_index_for_unknown_route(client, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><body><div id=\"root\">SPA</div></body></html>",
        encoding="utf-8",
    )
    container = client.app.state.container
    monkeypatch.setattr(container.settings, "frontend_dist", dist)
    for route in ("/dashboard", "/voice-profiles", "/settings", "/vod-explorer",
                  "/clips", "/ideas", "/editor"):
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} did not return SPA index"
        assert "SPA" in resp.text


def test_spa_fallback_serves_real_assets(client, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    (assets / "main.js").write_text("console.log('main');", encoding="utf-8")
    container = client.app.state.container
    monkeypatch.setattr(container.settings, "frontend_dist", dist)
    resp = client.get("/assets/main.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_api_404_does_not_trigger_spa_fallback(client, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    container = client.app.state.container
    monkeypatch.setattr(container.settings, "frontend_dist", dist)
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    # Must be JSON, not the SPA HTML.
    assert resp.headers["content-type"].startswith("application/json")
    assert "SPA" not in resp.text


def test_spa_fallback_rejects_traversal_outside_dist(client, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    container = client.app.state.container
    monkeypatch.setattr(container.settings, "frontend_dist", dist)
    # An encoded traversal should not escape the dist directory.
    resp = client.get("/..%2F..%2Fapp.py")
    # Either 404 or the SPA index (we accept either, but never the actual file).
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert "import" not in resp.text  # not the python source
