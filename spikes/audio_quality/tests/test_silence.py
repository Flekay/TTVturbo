"""Tests for silence detection and framing."""

from __future__ import annotations

import math

import numpy as np
import pytest

from analyzer import AudioQualityAnalyzer
from models import QualityConfig


def _signal_with_silence(sr: int, lead_ms: float, trail_ms: float, active_seconds: float = 1.0) -> np.ndarray:
    lead = int(lead_ms / 1000.0 * sr)
    trail = int(trail_ms / 1000.0 * sr)
    n_active = int(active_seconds * sr)
    t = np.linspace(0, active_seconds, n_active, endpoint=False)
    active = 0.3 * np.sin(2 * np.pi * 220 * t)
    return np.concatenate([np.zeros(lead), active, np.zeros(trail)]).astype(np.float64)


def test_leading_and_trailing_silence(write_wav, tmp_path):
    sr = 16000
    data = _signal_with_silence(sr, lead_ms=300, trail_ms=200, active_seconds=1.0)
    path = write_wav(tmp_path / "padded.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    # framing is hop-based (10 ms), allow one hop tolerance
    assert abs(res.silence.leading_silence_ms - 300) < 20
    assert abs(res.silence.trailing_silence_ms - 200) < 20
    assert res.silence.total_silence_ratio > 0
    assert res.silence.voice_ratio > 0


def test_pure_silence_is_reject(write_wav, tmp_path):
    sr = 16000
    data = np.zeros(sr, dtype=np.float64)
    path = write_wav(tmp_path / "silent.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.silence.total_silence_ratio >= 0.99
    assert res.quality.value == "REJECT"


def test_silence_threshold_cli_changes(write_wav, tmp_path):
    sr = 16000
    # very quiet tone below default -45 dBFS but above -60 dBFS
    amplitude = 10 ** (-50 / 20)
    t = np.linspace(0, 1, sr, endpoint=False)
    data = amplitude * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "quiet.wav", data, sample_rate=sr)

    res_default = AudioQualityAnalyzer(QualityConfig(silence_threshold_dbfs=-45)).analyze(path)
    res_loose = AudioQualityAnalyzer(QualityConfig(silence_threshold_dbfs=-60)).analyze(path)

    # at -45 threshold the quiet tone is silent -> reject
    assert res_default.silence.total_silence_ratio >= 0.99
    # at -60 threshold the quiet tone is active
    assert res_loose.silence.voice_ratio > 0.5


def test_total_silence_ratio_pure_tone(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "tone.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.silence.total_silence_ratio < 0.05
    assert res.silence.voice_ratio > 0.95
