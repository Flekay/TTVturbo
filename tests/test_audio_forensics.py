"""Tests for the ASR audio forensics module.

Generates small synthetic test media files with FFmpeg (stereo, mono,
multi-stream) and verifies:
  - artifact generation for all 5 variants;
  - metrics computation (peak, RMS, clipping, silence, speech regions);
  - atomic artifact storage;
  - invalid stream ID rejection;
  - path traversal protection;
  - missing source file handling;
  - list/get/delete operations.
"""

from __future__ import annotations

import json
import subprocess
import uuid as _u
from pathlib import Path

import pytest

from ttvturbo.media_processing.audio_forensics import (
    AUDIO_VARIANTS,
    AudioForensicsService,
    DIAGNOSTICS_SUBDIR,
    compute_audio_metrics,
    ffprobe_source_streams,
)
from ttvturbo.media_processing.sources import MediaSourceResolver


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, source_id: str, file_path: Path) -> None:
        self.source_type = "file_upload"
        self.source_id = source_id
        self.file_path = file_path
        self.file_name = file_path.name
        self.title = file_path.stem
        self.duration_seconds = 3.0
        self.profile_id = None
        self.profile_login = None
        self.download_status = "READY"
        self.vod_dir = file_path.parent
        self.vod = None


class _FakeResolver:
    def __init__(self, sources: dict[str, _FakeSource]) -> None:
        self.sources = sources
        self.upload_storage = None
        self.library_service = None

    def resolve(self, source_type: str, source_id: str):
        src = self.sources.get(source_id)
        if src is None:
            from ttvturbo.media_processing.schemas import MediaSourceNotFoundError
            raise MediaSourceNotFoundError(f"source not found: {source_id}")
        return src

    def get_source_dir(self, source_type: str, source_id: str) -> Path:
        return self.sources[source_id].file_path.parent

    def get_vod_dir(self, vod_id: str) -> Path:
        return self.sources[vod_id].file_path.parent


# ---------------------------------------------------------------------------
# FFmpeg helpers for test media generation
# ---------------------------------------------------------------------------


def _have_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not available")


def _make_stereo_wav(path: Path, duration: float = 3.0, sr: int = 48000) -> None:
    """Create a stereo WAV with different left/right content."""
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration}:sample_rate={sr}",
        "-f", "lavfi", "-i",
        f"sine=frequency=880:duration={duration}:sample_rate={sr}",
        "-filter_complex", "[0:a][1:a]amerge=inputs=2[a]",
        "-map", "[a]",
        "-ac", "2", "-ar", str(sr),
        "-c:a", "pcm_s16le",
        str(path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def _make_mono_wav(path: Path, duration: float = 3.0, sr: int = 48000) -> None:
    """Create a mono WAV."""
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration}:sample_rate={sr}",
        "-ac", "1", "-ar", str(sr),
        "-c:a", "pcm_s16le",
        str(path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def _make_multi_stream_mp4(path: Path, duration: float = 3.0, sr: int = 48000) -> None:
    """Create an MP4 with two audio streams."""
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration}:sample_rate={sr}",
        "-f", "lavfi", "-i",
        f"sine=frequency=880:duration={duration}:sample_rate={sr}",
        "-f", "lavfi", "-i",
        f"color=c=black:s=320x240:d={duration}",
        "-filter_complex",
        "[0:a]aformat=channel_layouts=mono[a0];"
        "[1:a]aformat=channel_layouts=mono[a1]",
        "-map", "[a0]", "-map", "[a1]", "-map", "2:v",
        "-c:a", "aac", "-b:a", "128k",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def _make_video_audio_mp4(path: Path, duration: float = 3.0, sr: int = 48000) -> None:
    """Create an MP4 with video at stream index 0 and stereo audio at index 1.

    This is the common real-world layout (e.g. Twitch clips, uploaded MP4s)
    where the audio stream's absolute index is 1, not 0. The FFmpeg map
    command must use the absolute index, not the relative audio index.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i",
        f"color=c=black:s=320x240:d={duration}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration}:sample_rate={sr}",
        "-f", "lavfi", "-i",
        f"sine=frequency=880:duration={duration}:sample_rate={sr}",
        "-filter_complex",
        "[1:a][2:a]amerge=inputs=2,aformat=channel_layouts=stereo[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def forensics_service(tmp_path: Path) -> tuple[AudioForensicsService, _FakeResolver]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    sources: dict[str, _FakeSource] = {}
    resolver = _FakeResolver(sources)
    svc = AudioForensicsService(data_dir, resolver)  # type: ignore[arg-type]
    return svc, resolver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stereo_source_generates_all_variants(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-stereo"
    src_path = Path(resolver.sources[src_id].file_path).parent / "stereo.wav" if False else None
    # Create source file.
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "stereo.wav"
    _make_stereo_wav(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    assert diag["source_type"] == "file_upload"
    assert diag["source_id"] == src_id
    artifacts = diag["artifacts"]
    assert set(artifacts.keys()) == set(AUDIO_VARIANTS)
    for variant in AUDIO_VARIANTS:
        art = artifacts[variant]
        assert art.get("error") is None, f"{variant} failed: {art.get('error')}"
        assert art["metrics"] is not None
        flac_path = svc.artifact_path(diag["id"], variant)
        assert flac_path.is_file(), f"{variant}.flac missing"
        # All artifacts should be mono 16kHz FLAC.
        m = art["metrics"]
        assert m["sample_rate"] == 16000
        assert m["channels"] == 1
        assert m["codec"] == "flac"
        assert m["duration_seconds"] is not None and m["duration_seconds"] > 0
        assert m["sha256"] is not None and len(m["sha256"]) == 64


def test_mono_source_generates_all_variants(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-mono"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "mono.wav"
    _make_mono_wav(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    artifacts = diag["artifacts"]
    for variant in AUDIO_VARIANTS:
        assert artifacts[variant].get("error") is None, f"{variant} failed"


def test_video_then_audio_source_generates_all_variants(forensics_service):
    """Video at index 0, audio at index 1 — the common real-world case.

    Regression test: the FFmpeg command must use absolute stream index
    ``-map 0:1`` (not ``-map 0:a:1`` which means "second audio stream"
    and fails when there's only one audio stream).
    """
    svc, resolver = forensics_service
    src_id = "src-video-audio"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "video_audio.mp4"
    _make_video_audio_mp4(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    # Verify the audio stream is at absolute index 1 (after video).
    audio_streams = diag["audio_streams"]
    assert len(audio_streams) == 1
    assert audio_streams[0]["index"] == 1  # video is at 0, audio at 1

    # All variants must succeed — no FFmpeg map errors.
    artifacts = diag["artifacts"]
    for variant in AUDIO_VARIANTS:
        art = artifacts[variant]
        assert art.get("error") is None, f"{variant} failed: {art.get('error')}"
        assert art["metrics"] is not None
        flac_path = svc.artifact_path(diag["id"], variant)
        assert flac_path.is_file(), f"{variant}.flac missing"


def test_multi_stream_source_lists_all_streams(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-multistream"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "multi.mp4"
    _make_multi_stream_mp4(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    audio_streams = diag["audio_streams"]
    assert len(audio_streams) >= 2
    indices = [s["index"] for s in audio_streams]
    assert sorted(indices) == sorted(set(indices))


def test_explicit_stream_id_selection(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-stream-select"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "multi.mp4"
    _make_multi_stream_mp4(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    # Use stream index 1 (second audio stream).
    diag = svc.create_diagnostic("file_upload", src_id, audio_stream_id=1)
    assert diag["audio_stream_id"] == 1


def test_invalid_stream_id_raises(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-invalid-stream"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "multi.mp4"
    _make_multi_stream_mp4(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    with pytest.raises(ValueError, match="invalid audio_stream_id"):
        svc.create_diagnostic("file_upload", src_id, audio_stream_id=99)


def test_missing_source_file_raises(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-missing"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "nonexistent.wav"
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    with pytest.raises(FileNotFoundError):
        svc.create_diagnostic("file_upload", src_id)


def test_path_traversal_diagnostic_id_rejected(forensics_service):
    svc, _ = forensics_service
    with pytest.raises(ValueError, match="invalid diagnostic id"):
        svc._diag_dir("../../../etc/passwd")  # noqa: SLF001


def test_list_and_get_diagnostic(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-list"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "stereo.wav"
    _make_stereo_wav(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    diags = svc.list_diagnostics()
    assert len(diags) == 1
    assert diags[0]["id"] == diag["id"]

    fetched = svc.get_diagnostic(diag["id"])
    assert fetched["id"] == diag["id"]


def test_delete_diagnostic(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-delete"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "stereo.wav"
    _make_stereo_wav(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    assert svc.delete_diagnostic(diag["id"]) is True
    with pytest.raises(FileNotFoundError):
        svc.get_diagnostic(diag["id"])


def test_metrics_include_peak_rms_clipping_silence(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-metrics"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "stereo.wav"
    _make_stereo_wav(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    m = diag["artifacts"]["current-asr-input"]["metrics"]
    # Sine wave should have a measurable peak and RMS.
    assert m["peak_dbfs"] is not None
    assert m["rms_dbfs"] is not None
    assert m["dc_offset"] is not None
    assert m["clipping_ratio"] is not None
    assert m["silence_ratio"] is not None


def test_ffprobe_source_streams_returns_audio_and_video(forensics_service):
    svc, resolver = forensics_service
    src_id = "src-probe"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "multi.mp4"
    _make_multi_stream_mp4(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    info = ffprobe_source_streams(src_file)
    assert len(info["audio_streams"]) >= 2
    assert len(info["video_streams"]) >= 1
    assert info["format"]["duration_seconds"] is not None


def test_artifacts_are_atomic_no_part_files(forensics_service):
    """After successful generation, no .part or .tmp files should remain."""
    svc, resolver = forensics_service
    src_id = "src-atomic"
    src_dir = svc.data_dir.parent / "sources" / src_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "stereo.wav"
    _make_stereo_wav(src_file)
    resolver.sources[src_id] = _FakeSource(src_id, src_file)

    diag = svc.create_diagnostic("file_upload", src_id)
    diag_dir = svc._diag_dir(diag["id"])  # noqa: SLF001
    files = list(diag_dir.iterdir())
    part_files = [f for f in files if f.suffix == ".part" or f.name.endswith(".tmp")]
    assert part_files == [], f"leftover temp files: {part_files}"
