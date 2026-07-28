"""Tests for preset-driven production transcription.

Verifies that:
  * the TranscriptionService uses the selected default preset's parameters
    when creating a new transcription job;
  * the job's ``options`` record the preset id and full preset params
    (provenance);
  * the worker_job dict forwarded to the subprocess includes the preset
    params so the worker actually uses them;
  * explicit per-request ``language`` / ``model`` overrides still take
    priority over the preset;
  * when no preset store is configured, the service falls back to its
    env-var / constructor defaults (legacy behaviour);
  * existing transcripts are never overwritten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ttvturbo.media_processing.asr_presets import (
    AsrDefaultPresetStore,
    MULTILINGUAL_LARGE_V3_QUALITY,
    MULTILINGUAL_LARGE_V3_TURBO,
)
from ttvturbo.media_processing.audio_extraction import AudioExtractionService
from ttvturbo.media_processing.gpu_lock import GpuLock
from ttvturbo.media_processing.sources import MediaSourceResolver
from ttvturbo.media_processing.storage import MediaJobStorage
from ttvturbo.media_processing.transcription import TranscriptionService


# ---------------------------------------------------------------------------
# Minimal fakes — we only test job creation and worker_job wiring, not the
# actual transcription (which requires faster-whisper + CUDA).
# ---------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, source_id: str) -> None:
        self.file_path = Path(f"/tmp/{source_id}.mp4")
        self.file_name = f"{source_id}.mp4"
        self.title = source_id
        self.duration_seconds = 10.0
        self.profile_id = None
        self.source_type = "file_upload"
        self.source_id = source_id


class _FakeResolver:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.upload_storage = None
        self.library_service = None

    def resolve(self, source_type: str, source_id: str) -> Any:
        return _FakeSource(source_id)

    def get_source_dir(self, source_type: str, source_id: str) -> Path:
        d = self.tmp_path / source_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_vod_dir(self, vod_id: str) -> Path:
        return self.get_source_dir("twitch_vod", vod_id)


class _FakeAudioService:
    """Always reports a ready audio artifact so no extraction job is spawned."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def get_audio_artifact(self, source_id: str, source_type: str) -> dict:
        return {"status": "READY", "source_id": source_id, "source_type": source_type}

    def artifact_path(self, source_id: str, source_type: str) -> Path:
        p = self.tmp_path / source_id / "audio.flac"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")
        return p

    def start_extraction(self, *a, **kw):
        raise RuntimeError("should not be called in this test")


@pytest.fixture()
def storage(tmp_path: Path) -> MediaJobStorage:
    return MediaJobStorage(tmp_path / "media_jobs")


@pytest.fixture()
def gpu_lock(tmp_path: Path) -> GpuLock:
    return GpuLock(tmp_path / "gpu_lock")


@pytest.fixture()
def resolver(tmp_path: Path) -> _FakeResolver:
    return _FakeResolver(tmp_path / "sources")


@pytest.fixture()
def audio_service(tmp_path: Path) -> _FakeAudioService:
    return _FakeAudioService(tmp_path / "audio")


@pytest.fixture()
def preset_store(tmp_path: Path) -> AsrDefaultPresetStore:
    return AsrDefaultPresetStore(tmp_path / "asr")


def _make_service(
    storage: MediaJobStorage,
    resolver: _FakeResolver,
    audio_service: _FakeAudioService,
    gpu_lock: GpuLock,
    preset_store: AsrDefaultPresetStore | None = None,
) -> TranscriptionService:
    return TranscriptionService(
        storage=storage,
        source_resolver=resolver,  # type: ignore[arg-type]
        audio_service=audio_service,  # type: ignore[arg-type]
        gpu_lock=gpu_lock,
        device="cpu",
        compute_type="int8",
        default_preset_store=preset_store,
    )


# ---------------------------------------------------------------------------


def test_job_uses_selected_preset_params(preset_store, storage, resolver, audio_service, gpu_lock):
    preset_store.select("multilingual-large-v3-quality")
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store)
    job = svc.start_transcription("file_upload", "src-1")
    opts = job["options"]
    assert opts["preset_id"] == "multilingual-large-v3-quality"
    assert opts["preset_params"] is not None
    assert opts["preset_params"]["model"] == "large-v3"
    assert opts["preset_params"]["compute_type"] == "float16"
    assert opts["preset_params"]["vad_filter"] is True
    assert opts["preset_params"]["beam_size"] == 5
    assert opts["preset_params"]["condition_on_previous_text"] is False
    # The preset's language is None (auto-detect); the service default
    # fills in only when the preset doesn't specify. Since the preset
    # has language=None, the effective language falls back to the
    # service default ("de").
    assert opts["language"] == "de"
    assert opts["model"] == "large-v3"


def test_job_uses_turbo_preset_params(preset_store, storage, resolver, audio_service, gpu_lock):
    preset_store.select("multilingual-large-v3-turbo")
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store)
    job = svc.start_transcription("file_upload", "src-1")
    opts = job["options"]
    assert opts["preset_id"] == "multilingual-large-v3-turbo"
    assert opts["preset_params"]["model"] == "large-v3-turbo"
    assert opts["preset_params"]["compute_type"] == "int8_float16"
    assert opts["preset_params"]["beam_size"] == 1
    assert opts["model"] == "large-v3-turbo"


def test_explicit_language_overrides_preset(preset_store, storage, resolver, audio_service, gpu_lock):
    preset_store.select("multilingual-large-v3-quality")
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store)
    job = svc.start_transcription("file_upload", "src-1", language="en")
    assert job["options"]["language"] == "en"
    # Preset is still recorded for provenance.
    assert job["options"]["preset_id"] == "multilingual-large-v3-quality"


def test_explicit_model_overrides_preset(preset_store, storage, resolver, audio_service, gpu_lock):
    preset_store.select("multilingual-large-v3-quality")
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store)
    job = svc.start_transcription("file_upload", "src-1", model="medium")
    assert job["options"]["model"] == "medium"
    assert job["options"]["preset_id"] == "multilingual-large-v3-quality"


def test_no_preset_store_falls_back_to_defaults(storage, resolver, audio_service, gpu_lock):
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store=None)
    job = svc.start_transcription("file_upload", "src-1")
    opts = job["options"]
    assert opts["preset_id"] is None
    assert opts["preset_params"] is None
    # Service defaults: language=de, model=large-v3.
    assert opts["language"] == "de"
    assert opts["model"] == "large-v3"


def test_worker_job_includes_preset_params(preset_store, storage, resolver, audio_service, gpu_lock):
    """The _spawn_worker method must forward preset_params to the worker."""
    preset_store.select("multilingual-large-v3-quality")
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store)
    job = svc.start_transcription("file_upload", "src-1")

    # _spawn_worker writes worker_job.json next to the job file. The job
    # is WAITING_FOR_GPU, so _spawn_worker was already called. Read the
    # worker_job.json to verify the preset params were forwarded.
    job_dir = storage._job_dir(job["id"])  # noqa: SLF001
    worker_job_path = job_dir / "worker_job.json"
    assert worker_job_path.is_file(), "worker_job.json was not written"
    wjob = json.loads(worker_job_path.read_text(encoding="utf-8"))
    assert wjob["preset_params"] is not None
    assert wjob["preset_params"]["model"] == "large-v3"
    assert wjob["preset_params"]["compute_type"] == "float16"
    assert wjob["preset_params"]["beam_size"] == 5
    assert wjob["device"] == "cuda"  # preset device, not the service's "cpu"
    assert wjob["compute_type"] == "float16"  # preset compute_type


def test_worker_job_without_preset_has_no_preset_params(storage, resolver, audio_service, gpu_lock):
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store=None)
    job = svc.start_transcription("file_upload", "src-1")
    job_dir = storage._job_dir(job["id"])  # noqa: SLF001
    wjob = json.loads((job_dir / "worker_job.json").read_text(encoding="utf-8"))
    assert "preset_params" not in wjob or wjob["preset_params"] is None
    assert wjob["device"] == "cpu"
    assert wjob["compute_type"] == "int8"


def test_existing_transcripts_not_overwritten(preset_store, storage, resolver, audio_service, gpu_lock, tmp_path):
    """Starting a new transcription must not overwrite existing transcript files."""
    import uuid as _u
    preset_store.select("multilingual-large-v3-quality")
    svc = _make_service(storage, resolver, audio_service, gpu_lock, preset_store)

    # Create a fake existing transcript with a valid UUID.
    existing_tx_id = str(_u.uuid4())
    existing_dir = svc.transcript_dir("src-1", existing_tx_id, "file_upload")
    existing_dir.mkdir(parents=True, exist_ok=True)
    existing_file = existing_dir / "transcript.txt"
    existing_file.write_text("old transcript", encoding="utf-8")

    # Start a new transcription — it gets a new transcription_id.
    job = svc.start_transcription("file_upload", "src-1")
    new_tx_id = job["transcription_id"]
    assert new_tx_id != existing_tx_id

    # The old file is untouched.
    assert existing_file.read_text(encoding="utf-8") == "old transcript"
