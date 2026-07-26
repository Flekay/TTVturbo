"""Shared pytest fixtures for TTVturbo backend tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module


E2E_ENV = "TTVTURBO_RUN_QWEN_TTS_E2E"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: real Qwen3-TTS model run (downloads weights)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(E2E_ENV) == "1":
        return
    skip_e2e = pytest.mark.skip(reason=f"set {E2E_ENV}=1 to run the real model e2e test")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app_module.app)


@pytest.fixture()
def recordings_dir() -> Path:
    return app_module.RECORDINGS_DIR


def _make_real_wav(path: Path, duration: float = 1.0) -> None:
    """Write a real, valid PCM WAV file with the given duration in seconds."""
    sample_rate = 44100
    n_frames = int(sample_rate * duration)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytes([(i & 0xFF) for i in range(n_frames * 2)])
        wav.writeframes(frames)


@pytest.fixture()
def make_real_wav():
    return _make_real_wav


@pytest.fixture()
def isolated_recordings(recordings_dir: Path, make_real_wav):
    """Create a small set of isolated test recordings and clean them up afterwards."""
    stamp = int(time.time() * 1000)
    paths: list[Path] = []
    a = recordings_dir / f"test_a_{stamp}.wav"
    b = recordings_dir / f"test_b_{stamp}.wav"
    make_real_wav(a, duration=1.0)
    make_real_wav(b, duration=2.0)
    paths.extend([a, b])
    try:
        yield {"a": a, "b": b, "stamp": stamp}
    finally:
        for p in paths:
            try:
                p.unlink()
            except OSError:
                pass


@pytest.fixture()
def make_test_audio():
    """Generate a real opus/webm audio file with FFmpeg for upload tests."""

    def _make(out_path: Path, duration: float = 2.0) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("ffmpeg not available")
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={duration}",
            "-acodec", "libopus",
            out_path.as_posix(),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0 or not out_path.is_file():
            pytest.skip(
                "Could not generate test audio: "
                + proc.stderr.decode("utf-8", errors="replace")[-500:]
            )

    return _make
