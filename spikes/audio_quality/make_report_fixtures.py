"""Generate three fixture WAV files for REPORT.md analysis.

Run from the spike directory:

    python make_report_fixtures.py

Writes into ``fixtures/`` (which is gitignored for binary content except
``.gitkeep``).  The fixtures are intentionally small and deterministic.
"""

from __future__ import annotations

import os

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
os.makedirs(FIX, exist_ok=True)

SR = 44100


def _save(name: str, data: np.ndarray, subtype: str = "FLOAT") -> str:
    path = os.path.join(FIX, name)
    sf.write(path, data.astype(np.float64), SR, subtype=subtype)
    return path


def clean_voice_like() -> str:
    """8 s of modulated tone that resembles a clean voice recording."""

    t = np.linspace(0, 8.0, int(8.0 * SR), endpoint=False)
    # fundamental + harmonics, amplitude-modulated to mimic speech envelope
    sig = (
        0.35 * np.sin(2 * np.pi * 180 * t)
        + 0.15 * np.sin(2 * np.pi * 360 * t)
        + 0.07 * np.sin(2 * np.pi * 540 * t)
    )
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 2.5 * t)
    sig = sig * envelope * 0.6
    # 200 ms leading + 150 ms trailing silence
    lead = np.zeros(int(0.2 * SR))
    trail = np.zeros(int(0.15 * SR))
    data = np.concatenate([lead, sig, trail])
    return _save("clean_voice_like.wav", data)


def clipped_fixture() -> str:
    """Same basis signal but hard-clipped to introduce distortion."""

    t = np.linspace(0, 6.0, int(6.0 * SR), endpoint=False)
    sig = (
        0.5 * np.sin(2 * np.pi * 180 * t)
        + 0.25 * np.sin(2 * np.pi * 360 * t)
    )
    sig = sig * 2.5  # overdrive
    sig = np.clip(sig, -1.0, 1.0)  # hard clip at full scale -> |x| >= 0.999
    return _save("clipped_fixture.wav", sig)


def noisy_low_snr() -> str:
    """Clean tone plus broadband noise -> low SNR."""

    rng = np.random.default_rng(42)
    t = np.linspace(0, 5.0, int(5.0 * SR), endpoint=False)
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    noise = 0.05 * rng.standard_normal(tone.size)
    return _save("noisy_low_snr.wav", tone + noise)


if __name__ == "__main__":
    for fn in (clean_voice_like, clipped_fixture, noisy_low_snr):
        print(fn())
