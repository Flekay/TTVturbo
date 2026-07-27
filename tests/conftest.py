"""Shared pytest fixtures for TTVturbo backend tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

import app as app_module


E2E_ENV = "TTVTURBO_RUN_QWEN_TTS_E2E"
E2E_TWITCH_ENV = "TTVTURBO_RUN_TWITCH_E2E"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: real Qwen3-TTS model run (downloads weights)")
    config.addinivalue_line("markers", "twitch_e2e: real Twitch API + VOD download run")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(E2E_ENV) != "1":
        skip_e2e = pytest.mark.skip(reason=f"set {E2E_ENV}=1 to run the real model e2e test")
        for item in items:
            if "e2e" in item.keywords and "twitch_e2e" not in item.keywords:
                item.add_marker(skip_e2e)
    if os.environ.get(E2E_TWITCH_ENV) != "1":
        skip_twitch = pytest.mark.skip(reason=f"set {E2E_TWITCH_ENV}=1 to run real Twitch tests")
        for item in items:
            if "twitch_e2e" in item.keywords:
                item.add_marker(skip_twitch)


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


# ---------------------------------------------------------------------------
# VOD-pipeline fixtures
# ---------------------------------------------------------------------------


def _ytdlp_entry(
    entry_id: str,
    title: str = "VOD",
    duration: float = 5400.0,
    view_count: int = 1234,
    timestamp: int = 1704067200,
    vod_type: str = "archive",
    url: Optional[str] = None,
) -> dict:
    """Build a normalized yt-dlp flat-playlist entry dict."""
    if url is None:
        url = f"https://www.twitch.tv/videos/{entry_id}"
    return {
        "id": entry_id,
        "url": url,
        "title": title,
        "thumbnail": f"https://example.test/{entry_id}.jpg",
        "duration": duration,
        "view_count": view_count,
        "timestamp": timestamp,
        "upload_date": "20240101",
        "type": vod_type,
    }


class FakeChannelLister:
    """In-memory stand-in for :class:`ChannelLister` (no yt-dlp subprocess)."""

    def __init__(self) -> None:
        self.vods_by_login: dict[str, list[dict]] = {}
        self.clips_by_login: dict[str, list[dict]] = {}
        self.info_by_url: dict[str, dict] = {}
        self.fail_vods_next = False
        self.fail_clips_next = False
        self.fail_info_next = False

    def add_vod(self, login: str, entry_id: str, **kwargs) -> dict:
        entry = _ytdlp_entry(entry_id, vod_type="archive", **kwargs)
        self.vods_by_login.setdefault(login.lower(), []).append(entry)
        self.info_by_url[entry["url"]] = entry
        return entry

    def add_clip(self, login: str, entry_id: str, **kwargs) -> dict:
        url = kwargs.pop("url", f"https://www.twitch.tv/{login}/clip/{entry_id}")
        entry = _ytdlp_entry(entry_id, vod_type="clip", url=url, **kwargs)
        self.clips_by_login.setdefault(login.lower(), []).append(entry)
        self.info_by_url[entry["url"]] = entry
        return entry

    def list_vods(self, login: str, limit: int = 100) -> list[dict]:
        if self.fail_vods_next:
            self.fail_vods_next = False
            from vod_pipeline import TwitchClientError
            raise TwitchClientError("yt-dlp boom")
        return list(self.vods_by_login.get(login.lower(), []))[:limit]

    def list_clips(self, login: str, limit: int = 100) -> list[dict]:
        if self.fail_clips_next:
            self.fail_clips_next = False
            from vod_pipeline import TwitchClientError
            raise TwitchClientError("yt-dlp boom")
        return list(self.clips_by_login.get(login.lower(), []))[:limit]

    def get_video_info(self, url: str) -> dict:
        if self.fail_info_next:
            self.fail_info_next = False
            from vod_pipeline import TwitchClientError
            raise TwitchClientError("yt-dlp boom")
        info = self.info_by_url.get(url)
        if info is None:
            from vod_pipeline import TwitchNotFoundError
            raise TwitchNotFoundError(f"not found: {url}")
        return info


@pytest.fixture()
def vod_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ttvturbo_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def vod_download_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vods_dl"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def channel_lister() -> FakeChannelLister:
    return FakeChannelLister()


@pytest.fixture()
def vod_service(vod_data_dir: Path, vod_download_dir: Path, channel_lister: FakeChannelLister):
    from vod_pipeline import VodPipelineStorage
    from vod_pipeline.service import VodPipelineService
    storage = VodPipelineStorage(vod_data_dir)
    return VodPipelineService(
        storage=storage,
        channel_lister=channel_lister,
        download_dir=vod_download_dir,
        max_concurrent=1,
        timeout_seconds=0.0,
        sync_limit=100,
    )


@pytest.fixture()
def vod_service_with_library(vod_data_dir: Path, vod_download_dir: Path, channel_lister: FakeChannelLister):
    """A VodPipelineService wired to a real LibraryService."""
    from vod_pipeline import VodPipelineStorage
    from vod_pipeline.service import VodPipelineService
    from library import LibraryService, LibraryStorage
    storage = VodPipelineStorage(vod_data_dir)
    library_service = LibraryService(LibraryStorage(vod_data_dir / "library"))
    return VodPipelineService(
        storage=storage,
        channel_lister=channel_lister,
        download_dir=vod_download_dir,
        max_concurrent=1,
        timeout_seconds=0.0,
        sync_limit=100,
        library_service=library_service,
    )


@pytest.fixture()
def make_real_mp4():
    """Generate a tiny, FFprobe-verifiable MP4 with ffmpeg."""
    def _make(path: Path, duration_seconds: float = 1.0) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            pytest.skip("ffmpeg not available")
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration_seconds}:size=160x120:rate=10",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={duration_seconds}",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            str(path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0 or not path.is_file():
            pytest.skip("could not generate test mp4: " + proc.stderr.decode("utf-8", errors="replace")[-300:])
    return _make

