"""Shared pytest fixtures for the audio quality spike.

Fixtures are generated in-memory and written to ``tmp_path`` so no large
audio files are committed to the repository.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

# Make sibling modules importable when running from this directory.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (_ROOT, str(_ROOT)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


SR = 16000


def _write_wav(path: Path, data: np.ndarray, sample_rate: int = SR, subtype: str = "FLOAT") -> str:
    """Write a WAV file.  ``data`` must be float in [-1, 1]."""

    if data.ndim == 1:
        sf.write(str(path), data.astype(np.float64), sample_rate, subtype=subtype)
    else:
        sf.write(str(path), data.astype(np.float64), sample_rate, subtype=subtype)
    return str(path)


@pytest.fixture
def write_wav():
    return _write_wav


@pytest.fixture
def sr():
    return SR


@pytest.fixture
def t():
    """Time vector for one second at SR."""

    return np.linspace(0.0, 1.0, SR, endpoint=False)


# --- reusable signal builders --------------------------------------------

def silence(seconds: float, sample_rate: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sample_rate), dtype=np.float64)


def sine(freq: float, seconds: float, amplitude: float, sample_rate: int = SR) -> np.ndarray:
    n = int(seconds * sample_rate)
    t = np.linspace(0.0, seconds, n, endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float64)


@pytest.fixture
def make_sine():
    return sine


@pytest.fixture
def make_silence():
    return silence
