"""Backend integration tests for the voice-profile FastAPI endpoints.

These tests exercise the real router + service + storage stack via the
FastAPI TestClient. The app's global ``voice_profile_service`` is replaced
with one backed by a temp directory so tests never touch the real
``voice_profiles_data/`` folder. The script library uses the real pack
files under ``config/voice_lab/`` so the API responses mirror production.

The quality analyzer is replaced with a deterministic stub so the
attach-reference endpoint can be tested without running the real
``voice_clone.quality`` analyzer on synthetic WAVs.
"""

from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ttvturbo.voice_profiles_api import build_router, build_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_tone_wav(path: Path, duration: float = 1.0, sample_rate: int = 22050) -> None:
    """Write a small non-silent PCM WAV so the stub analyzer is plausible."""
    n = int(sample_rate * duration)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        # A simple ramp signal (non-silent, non-clipping) as signed 16-bit PCM.
        frames = bytearray()
        for i in range(n):
            val = (i * 2) & 0xFFFF
            if val >= 0x8000:
                val -= 0x10000
            frames += val.to_bytes(2, "little", signed=True)
        wav.writeframes(bytes(frames))


@pytest.fixture()
def isolated_voice_profiles(tmp_path: Path, recordings_dir: Path, app):
    """Replace the app's voice_profile_service with a temp-backed one.

    The router's endpoints reference the service through a mutable state
    container (``router.state``), so swapping the service there is enough —
    no router rebuild or route duplication.
    """
    vp_dir = tmp_path / "voice_profiles_data"
    vp_dir.mkdir(parents=True, exist_ok=True)
    service = build_service(
        recordings_dir=recordings_dir,
        voice_profiles_dir=vp_dir,
    )
    # Deterministic stub analyzer: returns a GOOD quality payload so
    # attach-reference succeeds without running the real analyzer.
    def _stub_analyzer(_filename: str) -> dict:
        return {
            "technical": {"sample_rate": 22050, "duration_seconds": 1.0},
            "voice_clone_reference": {
                "eligible": True,
                "quality": "GOOD",
                "reasons": [],
                "warnings": [],
            },
            "quality": "GOOD",
        }

    router = app.state.container.voice_profiles_router
    original_state = dict(router.state)
    original_service = app.state.container.voice_profile_service
    router.state["service"] = service
    router.state["quality_analyzer"] = _stub_analyzer
    app.state.container.voice_profile_service = service
    try:
        yield {"service": service, "vp_dir": vp_dir, "analyzer": _stub_analyzer}
    finally:
        router.state["service"] = original_state["service"]
        router.state["quality_analyzer"] = original_state["quality_analyzer"]
        app.state.container.voice_profile_service = original_service


@pytest.fixture()
def sample_wav(recordings_dir: Path):
    """Write a sample WAV into the real recordings dir and clean it up."""
    name = f"vp_api_test_{__import__('uuid').uuid4().hex}.wav"
    path = recordings_dir / name
    _write_tone_wav(path)
    try:
        yield name
    finally:
        try:
            path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Scripts endpoints
# ---------------------------------------------------------------------------


class TestScriptsEndpoints:
    def test_list_scripts_returns_real_pack(self, client, isolated_voice_profiles):
        resp = client.get("/api/voice-profiles/scripts")
        assert resp.status_code == 200
        body = resp.json()
        assert "pack" in body
        assert "prompts" in body
        # The real pack has 88 prompts.
        assert len(body["prompts"]) == 88
        assert body["pack"]["prompt_count"] == 88
        assert body["pack"]["locale"] == "de-DE"
        # Each prompt has the required fields.
        p0 = body["prompts"][0]
        assert "id" in p0
        assert "order" in p0
        assert "text" in p0
        assert "recommended_duration_seconds" in p0
        assert isinstance(p0["recommended_duration_seconds"], dict)


# ---------------------------------------------------------------------------
# Profile CRUD
# ---------------------------------------------------------------------------


class TestProfileCRUD:
    def test_create_and_list_profile(self, client, isolated_voice_profiles):
        # Initially empty.
        resp = client.get("/api/voice-profiles")
        assert resp.status_code == 200
        assert resp.json() == {"profiles": []}

        # Create.
        resp = client.post("/api/voice-profiles", json={"name": "Test", "locale": "de-DE"})
        assert resp.status_code == 201
        profile = resp.json()
        assert profile["name"] == "Test"
        assert profile["locale"] == "de-DE"
        assert profile["id"]
        # Progress is computed for the real 88-prompt pack.
        assert profile["progress"]["total"] == 88
        assert profile["progress"]["missing"] == 88
        assert profile["progress"]["accepted"] == 0
        # References is a dict (empty).
        assert profile["references"] == {}

        # List now contains the profile.
        resp = client.get("/api/voice-profiles")
        assert resp.status_code == 200
        profiles = resp.json()["profiles"]
        assert len(profiles) == 1
        assert profiles[0]["id"] == profile["id"]

    def test_get_single_profile(self, client, isolated_voice_profiles):
        pid = client.post(
            "/api/voice-profiles", json={"name": "Single", "locale": "de-DE"}
        ).json()["id"]
        resp = client.get(f"/api/voice-profiles/{pid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == pid

    def test_get_unknown_profile_returns_404(self, client, isolated_voice_profiles):
        resp = client.get("/api/voice-profiles/nope")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["code"] == "voice_profile_not_found"

    def test_patch_rename(self, client, isolated_voice_profiles):
        pid = client.post(
            "/api/voice-profiles", json={"name": "Old", "locale": "de-DE"}
        ).json()["id"]
        resp = client.patch(f"/api/voice-profiles/{pid}", json={"name": "New"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"

    def test_patch_with_empty_body_returns_400(self, client, isolated_voice_profiles):
        pid = client.post(
            "/api/voice-profiles", json={"name": "X", "locale": "de-DE"}
        ).json()["id"]
        resp = client.patch(f"/api/voice-profiles/{pid}", json={})
        assert resp.status_code == 400

    def test_delete_profile(self, client, isolated_voice_profiles):
        pid = client.post(
            "/api/voice-profiles", json={"name": "Del", "locale": "de-DE"}
        ).json()["id"]
        resp = client.delete(f"/api/voice-profiles/{pid}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # Gone.
        assert client.get(f"/api/voice-profiles/{pid}").status_code == 404

    def test_delete_unknown_profile_returns_404(self, client, isolated_voice_profiles):
        resp = client.delete("/api/voice-profiles/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class TestReferences:
    def test_attach_reference(self, client, isolated_voice_profiles, sample_wav):
        pid = client.post(
            "/api/voice-profiles", json={"name": "Refs", "locale": "de-DE"}
        ).json()["id"]
        # Pick the first script id from the pack.
        scripts = client.get("/api/voice-profiles/scripts").json()["prompts"]
        sid = scripts[0]["id"]
        resp = client.put(
            f"/api/voice-profiles/{pid}/references/{sid}",
            json={"recording_filename": sample_wav},
        )
        assert resp.status_code == 200
        profile = resp.json()
        assert sid in profile["references"]
        ref = profile["references"][sid]
        assert ref["recording_filename"] == sample_wav
        assert ref["status"] in ("ACCEPTED", "REVIEW", "REJECTED")
        assert ref["quality_class"]
        # Progress updated.
        assert profile["progress"]["recorded"] >= 1

    def test_attach_reference_unknown_script_returns_404(
        self, client, isolated_voice_profiles, sample_wav
    ):
        pid = client.post(
            "/api/voice-profiles", json={"name": "X", "locale": "de-DE"}
        ).json()["id"]
        resp = client.put(
            f"/api/voice-profiles/{pid}/references/nope",
            json={"recording_filename": sample_wav},
        )
        assert resp.status_code == 404

    def test_attach_reference_unknown_profile_returns_404(
        self, client, isolated_voice_profiles, sample_wav
    ):
        resp = client.put(
            "/api/voice-profiles/nope/references/s1",
            json={"recording_filename": sample_wav},
        )
        assert resp.status_code == 404

    def test_detach_reference(self, client, isolated_voice_profiles, sample_wav):
        pid = client.post(
            "/api/voice-profiles", json={"name": "Det", "locale": "de-DE"}
        ).json()["id"]
        scripts = client.get("/api/voice-profiles/scripts").json()["prompts"]
        sid = scripts[0]["id"]
        client.put(
            f"/api/voice-profiles/{pid}/references/{sid}",
            json={"recording_filename": sample_wav},
        )
        resp = client.delete(f"/api/voice-profiles/{pid}/references/{sid}")
        assert resp.status_code == 200
        assert sid not in resp.json()["references"]

    def test_accept_review_reference(self, client, isolated_voice_profiles, sample_wav):
        pid = client.post(
            "/api/voice-profiles", json={"name": "Acc", "locale": "de-DE"}
        ).json()["id"]
        scripts = client.get("/api/voice-profiles/scripts").json()["prompts"]
        sid = scripts[0]["id"]
        # Attach first.
        client.put(
            f"/api/voice-profiles/{pid}/references/{sid}",
            json={"recording_filename": sample_wav},
        )
        # Accept-review is idempotent-ish: it forces ACCEPTED.
        resp = client.post(
            f"/api/voice-profiles/{pid}/references/{sid}/accept-review"
        )
        assert resp.status_code == 200
        assert resp.json()["references"][sid]["status"] == "ACCEPTED"
        assert resp.json()["references"][sid]["review_accepted"] is True


# ---------------------------------------------------------------------------
# Recording delete protection
# ---------------------------------------------------------------------------


class TestRecordingDeleteProtection:
    def test_delete_recording_blocked_when_referenced(
        self, client, isolated_voice_profiles, sample_wav
    ):
        pid = client.post(
            "/api/voice-profiles", json={"name": "Prot", "locale": "de-DE"}
        ).json()["id"]
        scripts = client.get("/api/voice-profiles/scripts").json()["prompts"]
        sid = scripts[0]["id"]
        client.put(
            f"/api/voice-profiles/{pid}/references/{sid}",
            json={"recording_filename": sample_wav},
        )
        # Now deleting the recording must be blocked with 409.
        resp = client.delete(f"/api/recordings/{sample_wav}")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "recording_in_use"
        assert any(p["id"] == pid for p in detail["profiles"])

    def test_delete_recording_allowed_when_not_referenced(
        self, client, isolated_voice_profiles, sample_wav
    ):
        # No profile references this recording.
        resp = client.delete(f"/api/recordings/{sample_wav}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True


# ---------------------------------------------------------------------------
# Status API
# ---------------------------------------------------------------------------


class TestStatusApi:
    def test_status_includes_voice_profiles(self, client, isolated_voice_profiles):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "voice_profiles" in body
        assert body["voice_profiles"]["count"] == 0
        # Create one and re-check.
        client.post(
            "/api/voice-profiles", json={"name": "S", "locale": "de-DE"}
        )
        body = client.get("/api/status").json()
        assert body["voice_profiles"]["count"] == 1
        assert body["features"]["voice_profiles"] == "available"
