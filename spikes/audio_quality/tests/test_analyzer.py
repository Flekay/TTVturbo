"""End-to-end tests for the analyzer: stereo, sample rates, NaN, broken
files, very short files, dropouts, JSON serialization, and the CLI."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from analyzer import AnalysisError, AudioQualityAnalyzer
from models import Quality, QualityConfig

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ---------------------------------------------------------------------------
# Stereo / multi-channel
# ---------------------------------------------------------------------------

def test_stereo_is_mixed_to_mono(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    left = 0.4 * np.sin(2 * np.pi * 220 * t)
    right = 0.4 * np.sin(2 * np.pi * 220 * t)
    stereo = np.stack([left, right], axis=1)
    path = write_wav(tmp_path / "stereo.wav", stereo, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.technical.channels == 2
    # mono mixdown = (L+R)/2 = 0.4 sine -> peak ~ -7.96 dBFS
    assert res.levels.peak_dbfs is not None
    assert math.isclose(res.levels.peak_dbfs, 20 * math.log10(0.4), abs_tol=0.05)


# ---------------------------------------------------------------------------
# Sample rates
# ---------------------------------------------------------------------------

def test_different_sample_rates(write_wav, tmp_path):
    for sr in (8000, 16000, 22050, 44100, 48000):
        t = np.linspace(0, 1, sr, endpoint=False)
        data = 0.3 * np.sin(2 * np.pi * 220 * t)
        path = write_wav(tmp_path / f"tone_{sr}.wav", data, sample_rate=sr)
        res = AudioQualityAnalyzer().analyze(path)
        assert res.technical.sample_rate == sr
        assert res.technical.duration_seconds == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# NaN / Infinity
# ---------------------------------------------------------------------------

def test_nan_samples_reject(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    data[100] = float("nan")
    path = write_wav(tmp_path / "nan.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.integrity.has_nan is True
    assert res.quality.value == "REJECT"


def test_inf_samples_reject(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    data[100] = float("inf")
    path = write_wav(tmp_path / "inf.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.integrity.has_infinity is True
    assert res.quality.value == "REJECT"


# ---------------------------------------------------------------------------
# Broken / unreadable file
# ---------------------------------------------------------------------------

def test_broken_file_raises(tmp_path):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"not a wav file at all")
    with pytest.raises(AnalysisError):
        AudioQualityAnalyzer().analyze(str(path))


def test_missing_file_raises(tmp_path):
    with pytest.raises(AnalysisError):
        AudioQualityAnalyzer().analyze(str(tmp_path / "does_not_exist.wav"))


# ---------------------------------------------------------------------------
# Very short file
# ---------------------------------------------------------------------------

def test_very_short_file_reject(write_wav, tmp_path):
    sr = 16000
    # 100 ms of tone -> shorter than absolute_min_seconds (1.0)
    n = int(0.1 * sr)
    t = np.linspace(0, 0.1, n, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "short.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.technical.duration_seconds < 1.0
    assert res.quality.value == "REJECT"


# ---------------------------------------------------------------------------
# Dropout inside active region
# ---------------------------------------------------------------------------

def test_dropout_in_active_region(write_wav, tmp_path):
    sr = 16000
    # 1 s of tone, then 50 ms of zeros, then 1 s of tone
    n1 = int(1.0 * sr)
    n_drop = int(0.05 * sr)
    t1 = np.linspace(0, 1, n1, endpoint=False)
    t2 = np.linspace(0, 1, n1, endpoint=False)
    data = np.concatenate([
        0.3 * np.sin(2 * np.pi * 220 * t1),
        np.zeros(n_drop),
        0.3 * np.sin(2 * np.pi * 220 * t2),
    ]).astype(np.float64)
    path = write_wav(tmp_path / "dropout.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.dropouts.dropout_count >= 1
    assert res.dropouts.longest_dropout_ms >= 40  # ~50 ms


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

def test_json_serialization_no_infinity(write_wav, tmp_path):
    sr = 16000
    data = np.zeros(sr, dtype=np.float64)
    path = write_wav(tmp_path / "silence.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    blob = json.dumps(res.to_dict())
    assert "Infinity" not in blob
    assert "NaN" not in blob
    parsed = json.loads(blob)
    assert parsed["levels"]["peak_dbfs"] is None
    assert parsed["levels"]["rms_dbfs"] is None


def test_json_serialization_clean_tone(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "tone.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    blob = json.dumps(res.to_dict())
    assert "Infinity" not in blob
    parsed = json.loads(blob)
    assert parsed["levels"]["peak_dbfs"] is not None
    assert parsed["technical"]["sample_rate"] == sr


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "analyze.py"), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_cli_clean_tone_exit_0(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "tone.wav", data, sample_rate=sr)
    out = tmp_path / "out.json"
    proc = _run_cli(path, "--output", str(out), "--pretty")
    assert proc.returncode in (0, 1)  # 1s tone shorter than 5s pref -> REVIEW
    assert out.exists()
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["technical"]["sample_rate"] == sr


def test_cli_silence_exit_2(write_wav, tmp_path):
    sr = 16000
    data = np.zeros(sr, dtype=np.float64)
    path = write_wav(tmp_path / "silence.wav", data, sample_rate=sr)
    proc = _run_cli(path, "--output", str(tmp_path / "out.json"))
    assert proc.returncode == 2


def test_cli_broken_file_exit_3(tmp_path):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"not a wav file")
    proc = _run_cli(str(path), "--output", str(tmp_path / "out.json"))
    assert proc.returncode == 3


def test_cli_missing_file_exit_3(tmp_path):
    proc = _run_cli(str(tmp_path / "missing.wav"))
    assert proc.returncode == 3


def test_cli_default_output_path(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "tone.wav", data, sample_rate=sr)
    proc = _run_cli(path)
    expected = str(tmp_path / "tone.analysis.json")
    assert os.path.exists(expected)
    assert proc.returncode in (0, 1, 2)


# ---------------------------------------------------------------------------
# Voice clone recommendation
# ---------------------------------------------------------------------------

def test_voice_clone_recommendation_block_present(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "tone.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    d = res.to_dict()
    vcr = d["voice_clone_reference"]
    assert "eligible" in vcr
    assert "quality" in vcr
    assert "reasons" in vcr
    assert "warnings" in vcr
    assert vcr["quality"] in [q.value for q in Quality]
