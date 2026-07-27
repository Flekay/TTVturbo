"""Tests for the VOD-pipeline FastAPI router and the Twitch status endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vod_pipeline import VodStatus
from vod_pipeline_api import build_router, build_twitch_status_router


@pytest.fixture()
def api_app(vod_service):
    app = FastAPI()
    app.include_router(build_router(vod_service))
    app.include_router(build_twitch_status_router(vod_service))
    return app


@pytest.fixture()
def api_client(api_app):
    return TestClient(api_app)


@pytest.fixture()
def profile_with_vods(api_client, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    channel_lister.add_vod("casepayt", "101")
    resp = api_client.post("/api/twitch/profiles", json={"login": "casepayt"})
    assert resp.status_code == 201
    profile = resp.json()
    sync = api_client.post(f"/api/twitch/profiles/{profile['id']}/sync-vods")
    assert sync.status_code == 200
    return profile


# ---------------------------------------------------------------------------
# Twitch status
# ---------------------------------------------------------------------------


def test_twitch_status_no_ytdlp(monkeypatch, vod_service, vod_data_dir, vod_download_dir):
    # Simulate yt-dlp not installed.
    import vod_pipeline_api as api_mod
    monkeypatch.setattr(api_mod, "_yt_dlp_version", lambda: None)
    app = FastAPI()
    app.include_router(build_twitch_status_router(vod_service))
    client = TestClient(app)
    resp = client.get("/api/twitch/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert "yt-dlp" in " ".join(data["reasons"])
    # No secrets/tokens in response.
    assert "token" not in resp.text


def test_twitch_status_available(api_client):
    resp = api_client.get("/api/twitch/status")
    assert resp.status_code == 200
    data = resp.json()
    # yt-dlp + ffprobe + writable dir -> available.
    assert data["downloader_available"] is True
    assert "yt_dlp_version" in data


def test_twitch_status_cached(api_client):
    r1 = api_client.get("/api/twitch/status")
    r2 = api_client.get("/api/twitch/status")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Same cached payload.
    assert r1.json() == r2.json()


# ---------------------------------------------------------------------------
# Profiles API
# ---------------------------------------------------------------------------


def test_api_list_profiles(api_client, profile_with_vods):
    resp = api_client.get("/api/twitch/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["profiles"]) == 1
    assert data["profiles"][0]["vod_count"] == 2


def test_api_create_profile_invalid(api_client):
    resp = api_client.post("/api/twitch/profiles", json={"login": ""})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "twitch_profile_validation"


def test_api_create_profile_from_url(api_client, channel_lister):
    resp = api_client.post(
        "/api/twitch/profiles", json={"url": "https://www.twitch.tv/casepayt"}
    )
    assert resp.status_code == 201
    assert resp.json()["login"] == "casepayt"


def test_api_get_profile(api_client, profile_with_vods):
    resp = api_client.get(f"/api/twitch/profiles/{profile_with_vods['id']}")
    assert resp.status_code == 200
    assert resp.json()["login"] == "casepayt"


def test_api_get_profile_unknown(api_client):
    resp = api_client.get("/api/twitch/profiles/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_api_refresh_profile(api_client, profile_with_vods):
    resp = api_client.post(f"/api/twitch/profiles/{profile_with_vods['id']}/refresh")
    assert resp.status_code == 200
    assert resp.json()["login"] == "casepayt"


def test_api_delete_profile_with_vods_409(api_client, profile_with_vods):
    resp = api_client.delete(f"/api/twitch/profiles/{profile_with_vods['id']}")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "twitch_profile_conflict"
    assert detail["vod_count"] == 2


def test_api_delete_profile_ok(api_client):
    resp = api_client.post("/api/twitch/profiles", json={"login": "solo"})
    pid = resp.json()["id"]
    dele = api_client.delete(f"/api/twitch/profiles/{pid}")
    assert dele.status_code == 200
    assert dele.json()["deleted"] is True


def test_api_sync_vods(api_client, profile_with_vods):
    # Already synced in fixture; sync again -> unchanged.
    resp = api_client.post(f"/api/twitch/profiles/{profile_with_vods['id']}/sync-vods")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["created"] == 0


# ---------------------------------------------------------------------------
# VODs API
# ---------------------------------------------------------------------------


def test_api_list_vods(api_client, profile_with_vods):
    resp = api_client.get(f"/api/vods?profile_id={profile_with_vods['id']}")
    assert resp.status_code == 200
    assert len(resp.json()["vods"]) == 2


def test_api_list_vods_sort(api_client, profile_with_vods):
    resp = api_client.get("/api/vods?sort=oldest")
    assert resp.status_code == 200


def test_api_get_vod(api_client, profile_with_vods):
    vods = api_client.get(f"/api/vods?profile_id={profile_with_vods['id']}").json()["vods"]
    vid = vods[0]["id"]
    resp = api_client.get(f"/api/vods/{vid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == vid


def test_api_get_vod_unknown(api_client):
    resp = api_client.get("/api/vods/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_api_import_vod(api_client, profile_with_vods, channel_lister):
    channel_lister.add_vod("casepayt", "200")
    resp = api_client.post(
        "/api/vods/import",
        json={"profile_id": profile_with_vods["id"], "url": "https://www.twitch.tv/videos/200"},
    )
    assert resp.status_code == 201
    assert resp.json()["twitch_video_id"] == "200"


def test_api_import_clip(api_client, profile_with_vods, channel_lister):
    channel_lister.add_clip("casepayt", "ClipSlug1")
    resp = api_client.post(
        "/api/vods/import",
        json={
            "profile_id": profile_with_vods["id"],
            "url": "https://www.twitch.tv/casepayt/clip/ClipSlug1",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["type"] == "clip"


def test_api_import_vod_invalid_url(api_client, profile_with_vods):
    resp = api_client.post(
        "/api/vods/import",
        json={"profile_id": profile_with_vods["id"], "url": "https://youtube.com/x"},
    )
    assert resp.status_code == 400


def test_api_download_conflict_when_already_running(api_client, profile_with_vods, vod_service, tmp_path, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed")
    import textwrap, sys, subprocess, threading, time
    vods = api_client.get(f"/api/vods?profile_id={profile_with_vods['id']}").json()["vods"]
    vid = vods[0]["id"]
    # Patch the service to run a long fake worker.
    script = tmp_path / "fake.py"
    script.write_text(textwrap.dedent("""
        import json, sys, time
        job = json.load(open(sys.argv[1], encoding="utf-8"))
        time.sleep(10)
        sys.exit(0)
    """), encoding="utf-8")
    _install_fake_spawn(vod_service, script)
    api_client.post(f"/api/vods/{vid}/download")
    # Second VOD to attempt parallel.
    channel_lister = vod_service.lister
    channel_lister.add_vod("casepayt", "999")
    api_client.post(f"/api/twitch/profiles/{profile_with_vods['id']}/sync-vods")
    vods2 = api_client.get(f"/api/vods?profile_id={profile_with_vods['id']}").json()["vods"]
    other = [v for v in vods2 if v["id"] != vid][0]
    resp = api_client.post(f"/api/vods/{other['id']}/download")
    assert resp.status_code == 409
    api_client.post(f"/api/vods/{vid}/cancel")


def _install_fake_spawn(service, script):
    import json, sys, subprocess, threading
    def fake_spawn(vod_id, vod):
        vod_dir = service._vod_dir(vod_id)
        vod_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = service.storage._vod_path(vod_id)
        job = {"vod_id": vod_id, "source_url": vod.get("source_url", ""), "output_dir": str(vod_dir), "metadata_path": str(metadata_path)}
        job_path = vod_dir / "job.json"
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        log_path = service.storage.vod_worker_log_path(vod_id)
        log_fh = open(log_path, "wb", buffering=0)
        proc = subprocess.Popen([sys.executable, str(script), str(job_path)], stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        with service._lock:
            service._active[vod_id] = proc
            service._active_log_fh[vod_id] = log_fh
        threading.Thread(target=service._reap_worker, args=(vod_id, proc, log_fh), daemon=True).start()
    service._spawn_worker = fake_spawn


def test_api_file_endpoint_not_ready(api_client, profile_with_vods):
    vods = api_client.get(f"/api/vods?profile_id={profile_with_vods['id']}").json()["vods"]
    vid = vods[0]["id"]
    resp = api_client.get(f"/api/vods/{vid}/file")
    assert resp.status_code == 409


def test_api_delete_vod(api_client, profile_with_vods):
    vods = api_client.get(f"/api/vods?profile_id={profile_with_vods['id']}").json()["vods"]
    vid = vods[0]["id"]
    resp = api_client.delete(f"/api/vods/{vid}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_api_delete_vod_unknown(api_client):
    resp = api_client.delete("/api/vods/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_api_path_traversal_rejected(api_client):
    resp = api_client.get("/api/vods/..%2fetc/file")
    # Either 404 (not found) or 400/500 from storage validation - never 200.
    assert resp.status_code != 200


def test_api_no_secret_in_any_response(api_client, profile_with_vods):
    # No credentials exist anymore, but sweep for any token-like strings.
    endpoints = [
        "/api/twitch/status",
        "/api/twitch/profiles",
        f"/api/twitch/profiles/{profile_with_vods['id']}",
        "/api/vods",
    ]
    for ep in endpoints:
        text = api_client.get(ep).text
        assert "client_secret" not in text
        assert "fake-token" not in text
