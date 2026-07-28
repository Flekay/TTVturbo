"""Tests for the /api/status endpoint."""

from __future__ import annotations

from ttvturbo.settings import APP_NAME, APP_VERSION


def test_status_returns_online(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert data["app_name"] == APP_NAME
    assert data["version"] == APP_VERSION


def test_status_has_required_top_level_fields(client):
    data = client.get("/api/status").json()
    for field in ("status", "app_name", "version", "uptime_seconds",
                  "recordings", "storage", "features"):
        assert field in data, f"missing field {field}"


def test_status_uptime_is_real_and_non_negative(client):
    data = client.get("/api/status").json()
    assert isinstance(data["uptime_seconds"], (int, float))
    assert data["uptime_seconds"] >= 0.0


def test_status_recordings_count_matches_real_files(client, isolated_recordings):
    data = client.get("/api/status").json()
    recs_meta = data["recordings"]
    assert recs_meta["count"] >= 2
    # total_duration and total_size must be positive and consistent.
    assert recs_meta["total_duration_seconds"] >= 3.0  # 1s + 2s minimum
    assert recs_meta["total_size_bytes"] > 0


def test_status_recordings_total_duration_is_real(client, isolated_recordings):
    data = client.get("/api/status").json()
    # The two isolated WAVs are 1s and 2s, so the total must be at least 3s.
    assert data["recordings"]["total_duration_seconds"] >= 3.0


def test_status_recordings_total_size_is_real(client, isolated_recordings):
    data = client.get("/api/status").json()
    expected_min = isolated_recordings["a"].stat().st_size + isolated_recordings["b"].stat().st_size
    assert data["recordings"]["total_size_bytes"] >= expected_min


def test_status_storage_free_bytes_is_real(client):
    data = client.get("/api/status").json()
    free = data["storage"]["free_bytes"]
    assert isinstance(free, int)
    assert free > 0


def test_status_features_recording_available(client, ffmpeg_available):
    data = client.get("/api/status").json()
    if ffmpeg_available:
        assert data["features"]["recording"] == "available"
    else:
        assert data["features"]["recording"] == "unavailable"


def test_status_features_not_implemented_modules(client):
    data = client.get("/api/status").json()
    features = data["features"]
    # voice_cloning availability is now driven by the real GPU runtime
    # diagnostics, not a hard-coded value. It must be one of the two
    # documented strings; which one depends on the host machine.
    assert features["voice_cloning"] in ("available", "unavailable")
    assert features["vod_analysis"] == "not_implemented"
    assert features["video_editor"] == "not_implemented"


def test_status_exposes_voice_clone_runtime_diagnostics(client):
    """The /api/status response must carry the real GPU/runtime
    diagnostics under voice_clone_runtime (additive, never breaks the
    frontend)."""
    data = client.get("/api/status").json()
    assert "voice_clone_runtime" in data
    vc = data["voice_clone_runtime"]
    for key in (
        "available",
        "device",
        "torch_version",
        "torch_cuda_version",
        "cuda_available",
        "device_name",
        "vram_total_bytes",
        "vram_free_bytes",
        "qwen_tts_importable",
        "reasons",
        "warnings",
    ):
        assert key in vc, key
    assert isinstance(vc["available"], bool)
    assert isinstance(vc["cuda_available"], bool)
    assert isinstance(vc["reasons"], list)


def test_status_does_not_expose_server_paths(client):
    text = client.get("/api/status").text
    # No absolute server paths should leak into the status response.
    assert "C:\\" not in text
    assert "/home/" not in text
    assert "recordings/" not in text  # no path fragments
