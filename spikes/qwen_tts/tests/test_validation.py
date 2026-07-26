"""No-model unit tests for the Qwen3-TTS spike.

These tests must NOT download the 1.7B model. They cover input validation,
output validation, the metrics JSON shape with an injected test result, and
the runtime's attention-backend probing logic with stubbed torch/qwen_tts.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

_SPIKE = Path(__file__).resolve().parents[1]
if str(_SPIKE) not in sys.path:
    sys.path.insert(0, str(_SPIKE))

import diagnostics as dx  # noqa: E402
from runtime import RuntimeMetrics, VramSnapshot  # noqa: E402


# --------------------------------------------------------------------- helpers
def _write_wav(path: Path, data: np.ndarray, sr: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sr)


def _tone(seconds: float, sr: int = 24000, freq: float = 220.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# --------------------------------------------------------------- input checks
def test_missing_reference_file_errors(tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(tmp_path / "does_not_exist.wav"),
        ref_text="irrelevant",
        text="Hallo",
        output_path=str(out),
    )
    assert not report.ok
    assert any("does not exist" in e for e in report.errors)


def test_empty_ref_text_errors(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    out = tmp_path / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(ref), ref_text="   ", text="Hallo", output_path=str(out)
    )
    assert not report.ok
    assert any("ref_text" in e for e in report.errors)


def test_empty_target_text_errors(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    out = tmp_path / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(ref), ref_text="Eine Referenz.", text="", output_path=str(out)
    )
    assert not report.ok
    assert any("target text" in e for e in report.errors)


def test_invalid_wav_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav file at all")
    out = tmp_path / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(bad), ref_text="x", text="y", output_path=str(out)
    )
    assert not report.ok
    assert any("not readable" in e for e in report.errors)


def test_too_short_reference_errors(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(1.0))  # 1s < 2s minimum
    out = tmp_path / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(ref), ref_text="x", text="y", output_path=str(out)
    )
    assert not report.ok
    assert any("too short" in e for e in report.errors)


def test_unwritable_output_path_errors(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    # A path whose parent is a regular file cannot be created/written.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    out = blocker / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(ref), ref_text="x", text="y", output_path=str(out)
    )
    assert not report.ok
    assert any("not writable" in e for e in report.errors)


def test_recommended_window_warning_only(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(3.0))  # >=2s but <5s recommended
    out = tmp_path / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(ref), ref_text="x", text="y", output_path=str(out)
    )
    assert report.ok  # soft warning only
    assert any("recommended" in w for w in report.warnings)


def test_too_long_target_text_errors(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    out = tmp_path / "out.wav"
    report = dx.validate_inputs(
        ref_audio=str(ref), ref_text="x", text="A" * 400, output_path=str(out)
    )
    assert not report.ok
    assert any("too long" in e for e in report.errors)


# --------------------------------------------------------------- output checks
def test_output_validation_rejects_missing_file(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    report = dx.validate_output(
        output_path=str(tmp_path / "missing.wav"),
        ref_audio_path=str(ref),
    )
    assert not report.ok
    assert any("does not exist" in e for e in report.errors)


def test_output_validation_rejects_silence(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    out = tmp_path / "out.wav"
    _write_wav(out, np.zeros(int(24000 * 2.0), dtype=np.float32))
    report = dx.validate_output(output_path=str(out), ref_audio_path=str(ref))
    assert not report.ok
    assert any("silent" in e for e in report.errors)


def test_output_validation_rejects_byte_identical_to_reference(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0))
    report = dx.validate_output(output_path=str(ref), ref_audio_path=str(ref))
    assert not report.ok
    assert any("byte-identical" in e for e in report.errors)


def test_output_validation_accepts_real_new_audio(tmp_path: Path) -> None:
    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(5.0, freq=220.0))
    out = tmp_path / "out.wav"
    _write_wav(out, _tone(2.0, freq=440.0))  # different content, audible
    report = dx.validate_output(output_path=str(out), ref_audio_path=str(ref))
    assert report.ok, report.errors


# --------------------------------------------------------- metrics JSON shape
def test_metrics_json_with_injected_result(tmp_path: Path) -> None:
    metrics = RuntimeMetrics(
        model_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        model_revision="abc123",
        device="cuda:0",
        dtype="bfloat16",
        attention_backend="sdpa",
        attention_backend_fallback="flash_attention_2 unavailable; using sdpa",
        reference_duration_seconds=8.4,
        output_duration_seconds=5.8,
        model_load_seconds=19.2,
        prompt_creation_seconds=1.3,
        generation_seconds=7.6,
        peak_vram_bytes=10240000000,
        peak_ram_bytes=21400000000,
        sample_rate=24000,
        output_sha256="deadbeef",
    )
    metrics.vram_before_load = VramSnapshot("before_load", 0, 0, 0, 12227)
    metrics.vram_peak_load = VramSnapshot("peak_load", 10240000000, 11000000000, 1227, 12227)
    metrics.vram_peak_generation = VramSnapshot("peak_generation", 9800000000, 10500000000, 1727, 12227)
    metrics.vram_after_release = VramSnapshot("after_release", 500000000, 600000000, 11627, 12227)

    payload = metrics.to_json_dict()
    payload["status"] = "REAL_MODEL_VERIFIED"

    out = tmp_path / "test.metrics.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = json.loads(out.read_text(encoding="utf-8"))
    required = {
        "model_id",
        "model_revision",
        "device",
        "dtype",
        "attention_backend",
        "reference_duration_seconds",
        "output_duration_seconds",
        "model_load_seconds",
        "prompt_creation_seconds",
        "generation_seconds",
        "peak_vram_bytes",
        "peak_ram_bytes",
        "sample_rate",
        "output_sha256",
        "status",
    }
    assert required.issubset(loaded.keys())
    assert loaded["status"] == "REAL_MODEL_VERIFIED"
    assert loaded["attention_backend"] == "sdpa"
    assert loaded["peak_vram_bytes"] == 10240000000


# ----------------------------------------------------- attention backend probe
def test_probe_attention_backend_falls_back_to_sdpa(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime as rt

    # Force flash_attn import to fail -> must select sdpa, never raise.
    monkeypatch.setitem(sys.modules, "flash_attn", None)
    backend, note = rt.probe_attention_backend()
    assert backend == "sdpa"
    assert "flash_attention_2" in note


def test_probe_attention_backend_uses_flash_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime as rt

    fake = types.ModuleType("flash_attn")
    monkeypatch.setitem(sys.modules, "flash_attn", fake)
    backend, note = rt.probe_attention_backend()
    assert backend == "flash_attention_2"
    assert note == ""


# --------------------------------------------------------------- e2e (gated)
@pytest.mark.e2e
def test_e2e_real_clone(tmp_path: Path) -> None:
    """Real model run. Only runs with TTVTURBO_RUN_QWEN_TTS_E2E=1.

    Passes only if a brand-new WAV file is produced by the model.
    """
    import clone as clone_mod

    ref = tmp_path / "ref.wav"
    _write_wav(ref, _tone(6.0))
    out = tmp_path / "out.wav"
    metrics_path = tmp_path / "test.metrics.json"

    rc = clone_mod.main(
        [
            "--ref-audio", str(ref),
            "--ref-text", "Eine kurze Referenzaufnahme fuer den Spike.",
            "--text", "Das System verarbeitet die Aufnahme vollstaendig lokal.",
            "--language", "German",
            "--output", str(out),
            "--metrics", str(metrics_path),
        ]
    )
    assert rc == 0, f"clone.py exited with {rc}"
    assert out.exists(), "output WAV was not produced"
    # Must not be a copy of the reference.
    assert dx.file_sha256(str(out)) != dx.file_sha256(str(ref))

    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["status"] == "REAL_MODEL_VERIFIED"
    assert payload["output_duration_seconds"] > 0.5
    assert payload["sample_rate"] > 0
