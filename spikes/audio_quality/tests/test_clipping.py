"""Tests for clipping detection and configuration."""

from __future__ import annotations

import math

import numpy as np
import pytest

from analyzer import AudioQualityAnalyzer
from models import QualityConfig


def test_no_clipping_on_clean_tone(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.5 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "clean.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.levels.clipping_sample_count == 0
    assert res.levels.clipping_sample_ratio == 0.0


def test_clipping_threshold_configurable(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    # amplitude 0.9 -> no clipping at 0.999, but clipping at 0.8
    data = 0.9 * np.sin(2 * np.pi * 220 * t)
    path = write_wav(tmp_path / "amp.wav", data, sample_rate=sr)

    res_strict = AudioQualityAnalyzer(QualityConfig(clipping_magnitude=0.999)).analyze(path)
    res_loose = AudioQualityAnalyzer(QualityConfig(clipping_magnitude=0.8)).analyze(path)

    assert res_strict.levels.clipping_sample_count == 0
    assert res_loose.levels.clipping_sample_count > 0


def test_isolated_clipping_is_review(write_wav, tmp_path):
    cfg = QualityConfig(
        severe_clipping_ratio=1e-2,    # not severe
        isolated_clipping_ratio=1e-7,  # any clipping -> REVIEW
        reference_min_seconds=0.0,
        reference_max_seconds=10.0,
    )
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    # inject a handful of clipped samples
    for i in (1000, 5000, 9000):
        data[i] = 1.0
    path = write_wav(tmp_path / "iso.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer(cfg).analyze(path)
    assert res.levels.clipping_sample_count == 3
    assert res.quality.value == "REVIEW"


def test_severe_clipping_is_reject(write_wav, tmp_path):
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    data = np.clip(3.0 * np.sin(2 * np.pi * 220 * t), -1.0, 1.0)
    path = write_wav(tmp_path / "severe.wav", data, sample_rate=sr)
    res = AudioQualityAnalyzer().analyze(path)
    assert res.levels.clipping_sample_ratio > 1e-3
    assert res.quality.value == "REJECT"
