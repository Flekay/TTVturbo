"""Tests for level / clipping / DC-offset computation."""

from __future__ import annotations

import math

import numpy as np
import pytest

from analyzer import AudioQualityAnalyzer
from models import QualityConfig


def test_pure_silence_levels(write_wav, tmp_path):
    data = np.zeros(16000, dtype=np.float64)
    path = write_wav(tmp_path / "silence.wav", data)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.levels.peak_dbfs is None
    assert res.levels.rms_dbfs is None
    assert res.levels.dc_offset == 0.0
    assert res.levels.clipping_sample_count == 0
    assert res.quality.value == "REJECT"


def test_clean_sine_rms_dbfs(write_wav, tmp_path):
    # Full-scale sine: peak 0 dBFS, RMS = -3.01 dBFS
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.5 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "sine.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.levels.peak_dbfs is not None
    assert math.isclose(res.levels.peak_dbfs, 20 * math.log10(0.5), abs_tol=0.05)
    assert res.levels.rms_dbfs is not None
    # RMS of 0.5 sine = 0.5/sqrt(2)
    expected = 20 * math.log10(0.5 / math.sqrt(2))
    assert math.isclose(res.levels.rms_dbfs, expected, abs_tol=0.1)


def test_clipping_detection(write_wav, tmp_path):
    cfg = QualityConfig(clipping_magnitude=0.999)
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    # Overdrive sine that hard-clips at +/-1
    data = 2.0 * np.sin(2 * np.pi * 220 * t)
    data = np.clip(data, -1.0, 1.0)
    path = write_wav(tmp_path / "clipped.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer(cfg).analyze(path)
    assert res.levels.clipping_sample_count > 0
    assert res.levels.clipping_sample_ratio > 0
    # A heavily clipped full-scale sine should be REJECT (severe clipping)
    assert res.quality.value == "REJECT"


def test_dc_offset(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.1 * np.sin(2 * np.pi * 220 * t) + 0.2  # large DC offset
    path = write_wav(tmp_path / "dc.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert math.isclose(res.levels.dc_offset, 0.2, abs_tol=1e-3)


def test_peak_dbfs_full_scale(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = np.sin(2 * np.pi * 220 * t)  # full-scale sine
    path = write_wav(tmp_path / "fs.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.levels.peak_dbfs is not None
    assert math.isclose(res.levels.peak_dbfs, 0.0, abs_tol=1e-6)
