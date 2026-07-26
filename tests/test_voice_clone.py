"""Tests for the voice-clone vertical slice.

These tests do NOT load the Qwen3-TTS model. The worker subprocess is
simulated by monkeypatching ``VoiceCloneService._start_worker`` so the
metadata.json is written directly. This covers validation, path safety,
quality rejection, status persistence, subprocess failure, output checks,
delete, and server-restart recovery.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

import app as app_module
from voice_clone.schemas import GenerationStatus
from voice_clone.service import VoiceCloneService, ValidationError


# --------------------------------------------------------------------- helpers
def _write_wav(path: Path, data: np.ndarray, sr: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sr)


def _tone(seconds: float, sr: int = 24000, freq: float = 220.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silent(seconds: float, sr: int = 24000) -> np.ndarray:
    return np.zeros(int(sr * seconds), dtype=np.float32)


def _make_reference(recordings_dir: Path, name: str, seconds: float = 6.0) -> Path:
    """Write a real, audible reference WAV into the recordings directory."""
    path = recordings_dir / name
    _write_wav(path, _tone(seconds))
    return path


# --------------------------------------------------------------------- fixtures
@pytest.fixture()
def isolated_voice_clone(tmp_path: Path, recordings_dir: Path):
    """Replace the app's voice_clone_service with one using a temp dir.

    Returns a dict with the service, the temp voice_clones dir, and a helper
    to simulate the worker subprocess writing a final metadata state.
    """
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service = VoiceCloneService(
        recordings_dir=recordings_dir,
        voice_clones_dir=vc_dir,
    )
    original_service = app_module.voice_clone_service
    app_module.voice_clone_service = service
    try:
        yield {"service": service, "vc_dir": vc_dir}
    finally:
        app_module.voice_clone_service = original_service


@pytest.fixture()
def fake_worker(isolated_voice_clone):
    """Patch _start_worker so no real subprocess is spawned.

    Provides a function to control what the "worker" writes to metadata.json.
    By default the simulated worker writes a READY metadata + a real output
    WAV so tests can exercise the happy path.
    """
    service = isolated_voice_clone["service"]
    vc_dir = isolated_voice_clone["vc_dir"]

    # Track the most recent job so the test can inspect it.
    state: dict = {"job": None}

    def _fake_start_worker(generation_id: str, job_path: Path) -> None:
        with open(job_path, "r", encoding="utf-8") as fh:
            job = json.load(fh)
        state["job"] = job
        # Simulate the worker writing a READY result with a real output WAV.
        output_path = Path(job["output_path"])
        _write_wav(output_path, _tone(2.0, freq=440.0), sr=24000)
        meta = service._read_metadata(generation_id) or {}
        meta.update({
            "status": GenerationStatus.READY.value,
            "completed_at": service._now_iso(),
            "output_duration_seconds": 2.0,
            "generation_seconds": 1.0,
            "peak_vram_bytes": 1000,
            "model_revision": "test-revision",
        })
        service._write_metadata(generation_id, meta)
        # Release the slot immediately (the real reaper thread does this).
        service._release_slot(generation_id)

    patcher = patch.object(service, "_start_worker", side_effect=_fake_start_worker)
    patcher.start()
    try:
        yield state
    finally:
        patcher.stop()


# --------------------------------------------------------------------- status
def test_voice_clone_status_endpoint(client, isolated_voice_clone):
    resp = client.get("/api/voice-clone/status")
    assert resp.status_code == 200
    data = resp.json()
    # `available` is no longer hard-coded; it reflects the real GPU
    # runtime. We only assert the structural contract here, not the
    # value (which depends on whether torch+CUDA+qwen_tts are installed).
    assert isinstance(data["available"], bool)
    assert data["busy"] is False
    assert data["active_generation_id"] is None
    assert data["model_id"] == "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    # Additive diagnostic fields must be present.
    for key in ("device", "torch_version", "cuda_available", "reasons", "warnings"):
        assert key in data


# --------------------------------------------------------------------- validation
def test_create_generation_missing_reference(client, isolated_voice_clone):
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": "does_not_exist.wav",
            "reference_text": "Hallo",
            "target_text": "Welt",
        },
    )
    assert resp.status_code == 400
    assert "does not exist" in resp.json()["detail"]


def test_create_generation_path_traversal(client, isolated_voice_clone, recordings_dir):
    # Drop a file outside the recordings dir that a traversal attempt could target.
    outside = recordings_dir.parent / "outside_traversal_target.wav"
    _write_wav(outside, _tone(6.0))
    try:
        for bad in ("../outside_traversal_target.wav", "..\\outside_traversal_target.wav",
                    "subdir/../../outside_traversal_target.wav"):
            resp = client.post(
                "/api/voice-clone/generations",
                json={
                    "reference_recording": bad,
                    "reference_text": "Hallo",
                    "target_text": "Welt",
                },
            )
            assert resp.status_code == 400, f"accepted dangerous path: {bad}"
    finally:
        outside.unlink(missing_ok=True)


def test_create_generation_empty_reference_text(client, isolated_voice_clone, recordings_dir):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav")
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "   ",
            "target_text": "Welt",
        },
    )
    assert resp.status_code == 400
    assert "reference_text" in resp.json()["detail"]


def test_create_generation_empty_target_text(client, isolated_voice_clone, recordings_dir):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav")
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Hallo",
            "target_text": "",
        },
    )
    assert resp.status_code == 400
    assert "target_text" in resp.json()["detail"]


def test_create_generation_too_long_target_text(client, isolated_voice_clone, recordings_dir):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav")
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Hallo",
            "target_text": "A" * 400,
        },
    )
    assert resp.status_code == 400
    assert "too long" in resp.json()["detail"]


def test_create_generation_reject_quality(client, isolated_voice_clone, recordings_dir):
    # Near-silent WAV -> REJECT.
    ref = recordings_dir / f"silent_{uuid.uuid4().hex}.wav"
    _write_wav(ref, _silent(6.0))
    try:
        resp = client.post(
            "/api/voice-clone/generations",
            json={
                "reference_recording": ref.name,
                "reference_text": "Hallo",
                "target_text": "Welt",
            },
        )
        assert resp.status_code == 400
        assert "REJECT" in resp.json()["detail"]
    finally:
        ref.unlink(missing_ok=True)


def test_create_generation_review_quality_blocks_without_flag(
    client, isolated_voice_clone, recordings_dir
):
    # A 3s tone is REVIEW (shorter than the preferred 5s window) but not REJECT.
    ref = _make_reference(recordings_dir, f"short_{uuid.uuid4().hex}.wav", seconds=3.0)
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Hallo",
            "target_text": "Welt",
        },
    )
    assert resp.status_code == 400
    assert "REVIEW" in resp.json()["detail"]


def test_create_generation_review_quality_proceeds_with_flag(
    client, isolated_voice_clone, recordings_dir, fake_worker
):
    ref = _make_reference(recordings_dir, f"short_{uuid.uuid4().hex}.wav", seconds=3.0)
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Hallo",
            "target_text": "Welt",
            "allow_quality_warning": True,
        },
    )
    assert resp.status_code == 201
    gen_id = resp.json()["id"]
    # The fake worker writes READY synchronously.
    meta = client.get(f"/api/voice-clone/generations/{gen_id}").json()
    assert meta["status"] == GenerationStatus.READY.value


# --------------------------------------------------------------------- happy path
def test_create_generation_happy_path(client, isolated_voice_clone, fake_worker, recordings_dir):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Eine kurze Referenzaufnahme.",
            "target_text": "Das ist der Zieltext.",
            "allow_quality_warning": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    gen_id = data["id"]
    assert data["status"] == GenerationStatus.QUEUED.value

    # The fake worker wrote READY synchronously.
    meta = client.get(f"/api/voice-clone/generations/{gen_id}").json()
    assert meta["status"] == GenerationStatus.READY.value
    assert meta["reference_recording"] == ref.name
    assert meta["reference_text"] == "Eine kurze Referenzaufnahme."
    assert meta["target_text"] == "Das ist der Zieltext."
    assert meta["output_duration_seconds"] == 2.0
    assert meta["generation_seconds"] == 1.0
    assert meta["peak_vram_bytes"] == 1000
    assert meta["failure_reason"] is None
    assert meta["quality"]["quality"] in ("EXCELLENT", "GOOD", "REVIEW", "REJECT")


def test_list_generations_returns_metadata(client, isolated_voice_clone, fake_worker, recordings_dir):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Ref",
            "target_text": "Ziel",
            "allow_quality_warning": True,
        },
    )
    resp = client.get("/api/voice-clone/generations")
    assert resp.status_code == 200
    gens = resp.json()["generations"]
    assert len(gens) >= 1
    assert all("id" in g and "status" in g for g in gens)


def test_get_generation_404_when_missing(client, isolated_voice_clone):
    resp = client.get("/api/voice-clone/generations/doesnotexist0000000000000000000000000000")
    # Non-uuid path -> 400; valid uuid but missing -> 404.
    assert resp.status_code in (400, 404)


def test_get_generation_404_for_valid_uuid_missing(client, isolated_voice_clone):
    resp = client.get(f"/api/voice-clone/generations/{uuid.uuid4().hex}")
    assert resp.status_code == 404


# --------------------------------------------------------------------- audio
def test_audio_endpoint_returns_wav_when_ready(
    client, isolated_voice_clone, fake_worker, recordings_dir
):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Ref",
            "target_text": "Ziel",
            "allow_quality_warning": True,
        },
    )
    gen_id = resp.json()["id"]
    audio = client.get(f"/api/voice-clone/generations/{gen_id}/audio")
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")
    assert b"WAVE" in audio.content[:12]


def test_audio_endpoint_404_when_not_ready(client, isolated_voice_clone, recordings_dir):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    # Use a worker that leaves the job in FAILED state (no output.wav).
    service = isolated_voice_clone["service"]

    def _failing_worker(generation_id: str, job_path: Path) -> None:
        meta = service._read_metadata(generation_id) or {}
        meta["status"] = GenerationStatus.FAILED.value
        meta["failure_reason"] = "simulated subprocess failure"
        meta["completed_at"] = service._now_iso()
        service._write_metadata(generation_id, meta)
        service._release_slot(generation_id)

    with patch.object(service, "_start_worker", side_effect=_failing_worker):
        resp = client.post(
            "/api/voice-clone/generations",
            json={
                "reference_recording": ref.name,
                "reference_text": "Ref",
                "target_text": "Ziel",
                "allow_quality_warning": True,
            },
        )
        gen_id = resp.json()["id"]

    audio = client.get(f"/api/voice-clone/generations/{gen_id}/audio")
    assert audio.status_code == 404


# --------------------------------------------------------------------- failed subprocess
def test_failed_subprocess_records_failure_reason(
    client, isolated_voice_clone, recordings_dir
):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    service = isolated_voice_clone["service"]

    def _failing_worker(generation_id: str, job_path: Path) -> None:
        meta = service._read_metadata(generation_id) or {}
        meta["status"] = GenerationStatus.FAILED.value
        meta["failure_reason"] = "RuntimeError: simulated model crash"
        meta["completed_at"] = service._now_iso()
        service._write_metadata(generation_id, meta)
        service._release_slot(generation_id)

    with patch.object(service, "_start_worker", side_effect=_failing_worker):
        resp = client.post(
            "/api/voice-clone/generations",
            json={
                "reference_recording": ref.name,
                "reference_text": "Ref",
                "target_text": "Ziel",
                "allow_quality_warning": True,
            },
        )
        gen_id = resp.json()["id"]

    meta = client.get(f"/api/voice-clone/generations/{gen_id}").json()
    assert meta["status"] == GenerationStatus.FAILED.value
    assert "simulated model crash" in meta["failure_reason"]
    # A FAILED job must not have a seemingly-valid output.wav.
    out = service._output_path(gen_id)
    assert not out.is_file()


# --------------------------------------------------------------------- output validation
def test_output_file_check_rejects_byte_identical_to_reference(
    client, isolated_voice_clone, recordings_dir
):
    """If the worker produced a WAV byte-identical to the reference, the
    output validation must reject it and no output.wav may remain."""
    from voice_clone.runtime import validate_output

    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    errors, _ = validate_output(str(ref), str(ref))
    assert any("byte-identical" in e for e in errors)


def test_output_file_check_rejects_missing_file(tmp_path: Path):
    from voice_clone.runtime import validate_output

    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    errors, _ = validate_output(str(tmp_path / "missing.wav"), str(ref))
    assert any("does not exist" in e for e in errors)


def test_output_file_check_rejects_silence(tmp_path: Path):
    from voice_clone.runtime import validate_output

    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    out = tmp_path / "out.wav"
    _write_wav(out, _silent(2.0))
    errors, _ = validate_output(str(out), str(ref))
    assert any("silent" in e for e in errors)


def test_output_file_check_accepts_real_new_audio(tmp_path: Path):
    from voice_clone.runtime import validate_output

    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0, freq=220.0))
    out = tmp_path / "out.wav"
    _write_wav(out, _tone(2.0, freq=440.0))
    errors, _ = validate_output(str(out), str(ref))
    assert errors == []


# --------------------------------------------------------------------- delete
def test_delete_generation_removes_directory(
    client, isolated_voice_clone, fake_worker, recordings_dir
):
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    resp = client.post(
        "/api/voice-clone/generations",
        json={
            "reference_recording": ref.name,
            "reference_text": "Ref",
            "target_text": "Ziel",
            "allow_quality_warning": True,
        },
    )
    gen_id = resp.json()["id"]
    # Wait for the fake worker to finish (synchronous).
    meta = client.get(f"/api/voice-clone/generations/{gen_id}").json()
    assert meta["status"] == GenerationStatus.READY.value

    dele = client.delete(f"/api/voice-clone/generations/{gen_id}")
    assert dele.status_code == 200
    assert dele.json()["deleted"] is True
    # Directory is gone.
    assert not (isolated_voice_clone["vc_dir"] / gen_id).is_dir()
    # Subsequent get returns 404.
    assert client.get(f"/api/voice-clone/generations/{gen_id}").status_code == 404


def test_delete_generation_404_when_missing(client, isolated_voice_clone):
    resp = client.delete(f"/api/voice-clone/generations/{uuid.uuid4().hex}")
    assert resp.status_code == 404


def test_delete_generation_rejects_bad_id(client, isolated_voice_clone):
    resp = client.delete("/api/voice-clone/generations/not-a-uuid")
    assert resp.status_code == 400


# --------------------------------------------------------------------- concurrency
def test_second_generation_blocked_while_one_running(
    client, isolated_voice_clone, recordings_dir
):
    """While a generation is in progress, a second request must be rejected."""
    import threading

    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)
    service = isolated_voice_clone["service"]

    # A worker that holds the slot open until we release it. Like the real
    # _start_worker, it must return immediately and do its work in a thread.
    release_event = threading.Event()

    def _holding_worker(generation_id: str, job_path: Path) -> None:
        def _bg() -> None:
            release_event.wait(timeout=5.0)
            meta = service._read_metadata(generation_id) or {}
            meta["status"] = GenerationStatus.READY.value
            meta["completed_at"] = service._now_iso()
            _write_wav(Path(job_path).parent / "output.wav", _tone(2.0, freq=440.0), sr=24000)
            meta["output_duration_seconds"] = 2.0
            meta["generation_seconds"] = 1.0
            meta["peak_vram_bytes"] = 1000
            service._write_metadata(generation_id, meta)
            service._release_slot(generation_id)

        threading.Thread(target=_bg, daemon=True).start()

    with patch.object(service, "_start_worker", side_effect=_holding_worker):
        first = client.post(
            "/api/voice-clone/generations",
            json={
                "reference_recording": ref.name,
                "reference_text": "Ref",
                "target_text": "Ziel",
                "allow_quality_warning": True,
            },
        )
        assert first.status_code == 201
        # The slot is reserved (the holding worker has not released it).
        status = client.get("/api/voice-clone/status").json()
        assert status["busy"] is True

        second = client.post(
            "/api/voice-clone/generations",
            json={
                "reference_recording": ref.name,
                "reference_text": "Ref",
                "target_text": "Zweites Ziel",
                "allow_quality_warning": True,
            },
        )
        assert second.status_code == 409
        assert "already running" in second.json()["detail"]

        # Let the holding worker finish so the test does not hang.
        release_event.set()
        # Wait for the slot to clear.
        for _ in range(100):
            if not client.get("/api/voice-clone/status").json()["busy"]:
                break
            time.sleep(0.05)


# --------------------------------------------------------------------- restart recovery
def test_server_restart_marks_in_progress_as_failed(tmp_path: Path, recordings_dir: Path):
    """A generation left in a transient state must become FAILED after restart."""
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service1 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)

    # Manually create a generation directory with a GENERATING metadata,
    # simulating a crash mid-run. We bypass create_generation to avoid
    # starting a real subprocess.
    gen_id = uuid.uuid4().hex
    gen_dir = vc_dir / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": gen_id,
        "status": GenerationStatus.GENERATING.value,
        "reference_recording": ref.name,
        "reference_sha256": "",
        "reference_text": "Ref",
        "target_text": "Ziel",
        "language": "German",
        "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "model_revision": "unknown",
        "created_at": service1._now_iso(),
        "completed_at": None,
        "output_duration_seconds": None,
        "generation_seconds": None,
        "peak_vram_bytes": None,
        "quality": {},
        "failure_reason": None,
        "warnings": [],
    }
    service1._write_metadata(gen_id, meta)
    # Leave a partial output.wav to confirm it gets removed.
    (gen_dir / "output.wav").write_bytes(b"partial")

    # Simulate a restart by constructing a fresh service over the same dir.
    service2 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    recovered = service2.get_generation(gen_id)
    assert recovered is not None
    assert recovered["status"] == GenerationStatus.FAILED.value
    assert "restarted" in (recovered["failure_reason"] or "").lower()
    # Partial output must be gone.
    assert not (gen_dir / "output.wav").is_file()


def test_server_restart_preserves_ready_generation(tmp_path: Path, recordings_dir: Path):
    """A READY generation must survive a restart unchanged."""
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service1 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    ref = _make_reference(recordings_dir, f"ref_{uuid.uuid4().hex}.wav", seconds=6.0)

    gen_id = uuid.uuid4().hex
    gen_dir = vc_dir / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    _write_wav(gen_dir / "output.wav", _tone(2.0, freq=440.0), sr=24000)
    meta = {
        "id": gen_id,
        "status": GenerationStatus.READY.value,
        "reference_recording": ref.name,
        "reference_sha256": "abc",
        "reference_text": "Ref",
        "target_text": "Ziel",
        "language": "German",
        "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "model_revision": "rev1",
        "created_at": service1._now_iso(),
        "completed_at": service1._now_iso(),
        "output_duration_seconds": 2.0,
        "generation_seconds": 1.0,
        "peak_vram_bytes": 1000,
        "quality": {},
        "failure_reason": None,
        "warnings": [],
    }
    service1._write_metadata(gen_id, meta)

    service2 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    recovered = service2.get_generation(gen_id)
    assert recovered is not None
    assert recovered["status"] == GenerationStatus.READY.value
    assert recovered["output_duration_seconds"] == 2.0
    assert (gen_dir / "output.wav").is_file()


# --------------------------------------------------------------------- empty list
def test_list_generations_empty_when_none(client, isolated_voice_clone):
    resp = client.get("/api/voice-clone/generations")
    assert resp.status_code == 200
    assert resp.json()["generations"] == []


# --------------------------------------------------------------------- quality analyzer
def test_quality_analyzer_rejects_near_silent(tmp_path: Path):
    from voice_clone.quality import AudioQualityAnalyzer, Quality

    path = tmp_path / "silent.wav"
    _write_wav(path, _silent(6.0))
    result = AudioQualityAnalyzer().analyze(str(path))
    assert result.quality == Quality.REJECT


def test_quality_analyzer_reviews_short_reference(tmp_path: Path):
    from voice_clone.quality import AudioQualityAnalyzer, Quality

    path = tmp_path / "short.wav"
    _write_wav(path, _tone(3.0))
    result = AudioQualityAnalyzer().analyze(str(path))
    # A 3s tone is REVIEW (shorter than preferred 5s).
    assert result.quality == Quality.REVIEW


def test_quality_anizer_missing_file_raises(tmp_path: Path):
    from voice_clone.quality import AudioQualityAnalyzer, AnalysisError

    with pytest.raises(AnalysisError):
        AudioQualityAnalyzer().analyze(str(tmp_path / "missing.wav"))


# --------------------------------------------------------------------- e2e (gated)
@pytest.mark.e2e
def test_e2e_real_voice_clone_via_api(tmp_path: Path, recordings_dir: Path):
    """Real model run through the full HTTP API. Only runs with
    TTVTURBO_RUN_QWEN_TTS_E2E=1.

    Passes only if a brand-new WAV file is produced by the model and served
    back by the audio endpoint. Uses a real (isolated) VoiceCloneService so
    the actual subprocess and Qwen3-TTS runtime are exercised.
    """
    import time as _time

    from fastapi.testclient import TestClient
    import app as app_module
    from voice_clone.service import VoiceCloneService

    vc_dir = tmp_path / "voice_clones"
    service = VoiceCloneService(
        recordings_dir=recordings_dir,
        voice_clones_dir=vc_dir,
    )
    original = app_module.voice_clone_service
    app_module.voice_clone_service = service
    try:
        # Write a real reference WAV into the recordings directory.
        ref = recordings_dir / f"e2e_ref_{uuid.uuid4().hex}.wav"
        _write_wav(ref, _tone(6.0), sr=22050)
        try:
            client = TestClient(app_module.app)
            resp = client.post(
                "/api/voice-clone/generations",
                json={
                    "reference_recording": ref.name,
                    "reference_text": "Eine kurze Referenzaufnahme fuer den Test.",
                    "target_text": "Das System verarbeitet die Aufnahme vollstaendig lokal.",
                    "allow_quality_warning": True,
                },
            )
            assert resp.status_code == 201, resp.text
            gen_id = resp.json()["id"]

            # Poll until the worker finishes (real model load can take minutes).
            deadline = _time.time() + 600
            while _time.time() < deadline:
                meta = client.get(f"/api/voice-clone/generations/{gen_id}").json()
                status = meta["status"]
                if status in (GenerationStatus.READY.value, GenerationStatus.FAILED.value):
                    break
                _time.sleep(2)

            assert meta["status"] == GenerationStatus.READY.value, (
                f"generation did not finish READY: {meta.get('failure_reason')}"
            )
            assert meta["output_duration_seconds"] > 0.5
            assert meta["sample_rate"] if "sample_rate" in meta else meta.get("peak_vram_bytes", 0) >= 0

            audio = client.get(f"/api/voice-clone/generations/{gen_id}/audio")
            assert audio.status_code == 200
            assert audio.content.startswith(b"RIFF")
            assert b"WAVE" in audio.content[:12]

            # The output must not be byte-identical to the reference.
            from voice_clone.runtime import file_sha256

            out_path = service._output_path(gen_id)
            assert file_sha256(str(out_path)) != file_sha256(str(ref))
        finally:
            ref.unlink(missing_ok=True)
    finally:
        app_module.voice_clone_service = original
