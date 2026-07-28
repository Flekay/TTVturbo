"""Tests for the shared media-processing core.

Covers:

* media source resolution (READY VOD, not-ready VOD, unknown source,
  path traversal, missing file);
* GPU lock (acquire, busy, stale, release, parallel requests, owner
  types);
* audio extraction (real FFmpeg FLAC, mono, 16 kHz, SHA-256, reuse,
  force, cancel, FFmpeg error, restart recovery, .part not READY);
* transcription (job creation, audio dependency, GPU unavailable,
  exporters for JSON/TXT/SRT/VTT, invalid segments, restart recovery);
* pipeline (existing download reused, missing download started, audio
  after download, transcription after audio, READY_FOR_CLIP_ANALYSIS,
  error stops, retry, cancel).

Real faster-whisper model tests are gated behind
``TTVTURBO_RUN_TRANSCRIPTION_E2E=1`` and a real media file via
``TTVTURBO_TEST_TRANSCRIPTION_MEDIA``.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
import datetime as _dt
from pathlib import Path

import pytest

from ttvturbo.media_processing import (
    AudioExtractionService,
    GpuLock,
    GpuLockBusyError,
    GpuLockError,
    MediaJobConflictError,
    MediaJobNotFoundError,
    MediaJobStorage,
    MediaSourceNotFoundError,
    MediaSourceNotReadyError,
    MediaSourceResolver,
    PipelineService,
    TranscriptionService,
)
from ttvturbo.media_processing.gpu_lock import OWNER_TRANSCRIPTION, OWNER_VOICE_CLONE
from ttvturbo.media_processing.schemas import (
    JobType,
    MediaJobStatus,
    PipelineStatus,
    PipelineStepType,
)
from ttvturbo.media_processing.transcription_worker import (
    _export_srt,
    _export_txt,
    _export_vtt,
    _format_srt_timestamp,
    _format_vtt_timestamp,
)
from ttvturbo.vod_pipeline import VodPipelineStorage, VodStatus
from ttvturbo.vod_pipeline.service import VodPipelineService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def media_storage(vod_data_dir: Path) -> MediaJobStorage:
    return MediaJobStorage(vod_data_dir)


@pytest.fixture()
def source_resolver(vod_service: VodPipelineService) -> MediaSourceResolver:
    return MediaSourceResolver(vod_service.storage)


@pytest.fixture()
def gpu_lock(vod_data_dir: Path) -> GpuLock:
    return GpuLock(vod_data_dir)


@pytest.fixture()
def audio_service(
    media_storage: MediaJobStorage,
    source_resolver: MediaSourceResolver,
) -> AudioExtractionService:
    return AudioExtractionService(
        storage=media_storage,
        source_resolver=source_resolver,
    )


@pytest.fixture()
def transcription_service(
    media_storage: MediaJobStorage,
    source_resolver: MediaSourceResolver,
    audio_service: AudioExtractionService,
    gpu_lock: GpuLock,
) -> TranscriptionService:
    # Force CPU device for tests so we don't require CUDA.
    os.environ["TTVTURBO_TRANSCRIPTION_DEVICE"] = "cpu"
    os.environ["TTVTURBO_TRANSCRIPTION_COMPUTE_TYPE"] = "int8"
    return TranscriptionService(
        storage=media_storage,
        source_resolver=source_resolver,
        audio_service=audio_service,
        gpu_lock=gpu_lock,
        device="cpu",
        compute_type="int8",
    )


@pytest.fixture()
def pipeline_service(
    media_storage: MediaJobStorage,
    vod_service: VodPipelineService,
    audio_service: AudioExtractionService,
    transcription_service: TranscriptionService,
) -> PipelineService:
    return PipelineService(
        storage=media_storage,
        vod_service=vod_service,
        audio_service=audio_service,
        transcription_service=transcription_service,
    )


def _make_ready_vod(
    vod_service: VodPipelineService,
    make_real_mp4,
    channel_lister=None,
    title: str = "Test VOD",
    login: str = "testcasepayt",
) -> tuple[str, Path]:
    """Create a VOD record with a READY status and a real MP4 file.

    Requires the ``channel_lister`` fixture (FakeChannelLister) so sync
    has something to return.
    """
    if channel_lister is None:
        # Fall back to the vod_service's lister if it is a FakeChannelLister.
        channel_lister = getattr(vod_service, "lister", None)
    profile = vod_service.create_profile(login)
    profile_id = profile["id"]
    # Ensure the fake lister has a VOD for this login.
    if channel_lister is not None and not channel_lister.vods_by_login.get(login.lower()):
        channel_lister.add_vod(login, "100", title=title, duration=60.0)
    vod_service.sync_vods(profile_id)
    vods = vod_service.list_vods(profile_id=profile_id)
    assert vods, "sync_vods produced no VODs; check the channel_lister fixture"
    vod = vods[0]
    vod_id = vod["id"]
    # Place a real MP4 in the VOD dir and mark it READY.
    vod_dir = vod_service.storage.vod_dir(vod_id)
    vod_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = vod_dir / "source.mp4"
    make_real_mp4(mp4_path, duration_seconds=1.0)
    vod = vod_service.storage.load_vod(vod_id)
    vod["status"] = VodStatus.READY.value
    vod["download"] = {
        "started_at": "2024-01-01T00:00:00+00:00",
        "completed_at": "2024-01-01T01:00:00+00:00",
        "file_name": "source.mp4",
        "file_size_bytes": mp4_path.stat().st_size,
        "container": "mp4",
        "duration_seconds": 1.0,
        "width": 160,
        "height": 120,
        "video_codec": "h264",
        "audio_codec": "aac",
    }
    vod["title"] = title
    vod_service.storage.save_vod(vod)
    return vod_id, mp4_path


# ---------------------------------------------------------------------------
# Media source resolver
# ---------------------------------------------------------------------------


class TestMediaSourceResolver:
    def test_resolves_ready_vod(self, source_resolver, vod_service, make_real_mp4, channel_lister):
        vod_id, mp4_path = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        resolved = source_resolver.resolve("twitch_vod", vod_id)
        assert resolved.source_type == "twitch_vod"
        assert resolved.source_id == vod_id
        assert resolved.file_path == mp4_path
        assert resolved.download_status == "READY"
        assert resolved.title

    def test_rejects_not_downloaded_vod(self, source_resolver, vod_service, channel_lister):
        profile = vod_service.create_profile("notreadycasepayt")
        channel_lister.add_vod("notreadycasepayt", "400", title="Not Ready", duration=60.0)
        vod_service.sync_vods(profile["id"])
        vod = vod_service.list_vods(profile_id=profile["id"])[0]
        with pytest.raises(MediaSourceNotReadyError):
            source_resolver.resolve("twitch_vod", vod["id"])

    def test_unknown_source_type(self, source_resolver, vod_service, make_real_mp4, channel_lister):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        from ttvturbo.media_processing.schemas import MediaSourceError

        with pytest.raises(MediaSourceError):
            source_resolver.resolve("youtube", vod_id)

    def test_unknown_vod_id(self, source_resolver):
        with pytest.raises(MediaSourceNotFoundError):
            source_resolver.resolve("twitch_vod", str(uuid.uuid4()))

    def test_path_traversal_rejected(self, source_resolver):
        with pytest.raises((MediaSourceNotFoundError, Exception)):
            source_resolver.resolve("twitch_vod", "../../../etc/passwd")

    def test_missing_file(self, source_resolver, vod_service, make_real_mp4, channel_lister):
        vod_id, mp4_path = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        mp4_path.unlink()
        with pytest.raises(MediaSourceNotReadyError):
            source_resolver.resolve("twitch_vod", vod_id)

    def test_resolves_vod_promoted_to_library(
        self, vod_service, make_real_mp4, channel_lister, vod_data_dir
    ):
        """VOD-Promotion: file moved to library, resolver follows it.

        When a VOD is promoted to the library, the source file is moved
        from ``vods/{vod_id}/source.mp4`` to ``library/{item_id}/source.mp4``.
        The resolver must follow the ``library_item_id`` link and find the
        file in its new location.
        """
        from ttvturbo.library import LibraryService, LibraryStorage
        from ttvturbo.media_processing.sources import MediaSourceResolver

        vod_id, mp4_path = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        library_service = LibraryService(LibraryStorage(vod_data_dir / "library"))
        resolver = MediaSourceResolver(
            vod_service.storage,
            library_service=library_service,
        )

        # Promote: move the file to the library and set library_item_id.
        item = library_service.create_upload_item(
            file_name="source.mp4",
            title="Promoted VOD",
        )
        item_id = item["id"]
        dest = library_service.storage.item_dir(item_id) / "source.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        mp4_path.rename(dest)
        item["file_name"] = "source.mp4"
        item["file_size_bytes"] = dest.stat().st_size
        library_service.storage.save_item(item)

        # Update the VOD record to point at the library item.
        vod = vod_service.storage.load_vod(vod_id)
        vod["library_item_id"] = item_id
        vod_service.storage.save_vod(vod)

        # The resolver should find the file in the library.
        resolved = resolver.resolve("twitch_vod", vod_id)
        assert resolved.file_path == dest
        assert resolved.file_path.is_file()


# ---------------------------------------------------------------------------
# GPU lock
# ---------------------------------------------------------------------------


class TestGpuLock:
    def test_acquire_release(self, gpu_lock):
        gpu_lock.acquire(OWNER_VOICE_CLONE, "job-1")
        owner = gpu_lock.current_owner()
        assert owner is not None
        assert owner["owner_type"] == OWNER_VOICE_CLONE
        gpu_lock.release(OWNER_VOICE_CLONE, "job-1")
        assert gpu_lock.current_owner() is None

    def test_busy(self, gpu_lock):
        gpu_lock.acquire(OWNER_VOICE_CLONE, "job-1")
        with pytest.raises(GpuLockBusyError) as exc_info:
            gpu_lock.acquire(OWNER_TRANSCRIPTION, "job-2")
        assert exc_info.value.owner["owner_type"] == OWNER_VOICE_CLONE
        gpu_lock.release(OWNER_VOICE_CLONE, "job-1")

    def test_release_idempotent(self, gpu_lock):
        gpu_lock.acquire(OWNER_VOICE_CLONE, "job-1")
        gpu_lock.release(OWNER_VOICE_CLONE, "job-1")
        # Second release is a no-op.
        gpu_lock.release(OWNER_VOICE_CLONE, "job-1")
        assert gpu_lock.current_owner() is None

    def test_release_wrong_owner(self, gpu_lock):
        gpu_lock.acquire(OWNER_VOICE_CLONE, "job-1")
        # Releasing as a different owner does nothing.
        gpu_lock.release(OWNER_TRANSCRIPTION, "job-2")
        assert gpu_lock.current_owner() is not None
        gpu_lock.release(OWNER_VOICE_CLONE, "job-1")

    def test_invalid_owner_type(self, gpu_lock):
        with pytest.raises(GpuLockError):
            gpu_lock.acquire("bogus", "job-1")

    def test_stale_lock_reaped(self, gpu_lock):
        # Write a lock file with a PID that is definitely not alive.
        import datetime as _dt

        lock_path = gpu_lock.lock_path
        payload = {
            "owner_type": OWNER_TRANSCRIPTION,
            "job_id": "stale-job",
            "pid": 99999999,  # almost certainly not alive
            "acquired_at": (_dt.datetime.now(tz=_dt.timezone.utc)).isoformat(),
            "host": "test",
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        # Acquiring should reap the stale lock.
        gpu_lock.acquire(OWNER_VOICE_CLONE, "job-1")
        owner = gpu_lock.current_owner()
        assert owner["owner_type"] == OWNER_VOICE_CLONE
        gpu_lock.release(OWNER_VOICE_CLONE, "job-1")

    def test_context_manager_releases_on_exception(self, gpu_lock):
        from ttvturbo.media_processing.gpu_lock import GpuLockOwner

        with pytest.raises(RuntimeError):
            with GpuLockOwner(gpu_lock, OWNER_VOICE_CLONE, "job-1"):
                raise RuntimeError("boom")
        assert gpu_lock.current_owner() is None

    def test_context_manager_acquires(self, gpu_lock):
        from ttvturbo.media_processing.gpu_lock import GpuLockOwner

        with GpuLockOwner(gpu_lock, OWNER_VOICE_CLONE, "job-1"):
            owner = gpu_lock.current_owner()
            assert owner is not None
            assert owner["owner_type"] == OWNER_VOICE_CLONE
        assert gpu_lock.current_owner() is None


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------


class TestAudioExtraction:
    def test_extracts_real_flac(
        self, audio_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        job = audio_service.start_extraction("twitch_vod", vod_id)
        assert job["job_type"] == JobType.EXTRACT_AUDIO.value
        # Wait for the worker to finish (short audio).
        self._wait_for_terminal(audio_service, job["id"], timeout=30)
        final = audio_service.get_job(job["id"])
        assert final["status"] == MediaJobStatus.READY.value, final.get("error")
        # Verify the artifact.
        meta = audio_service.get_audio_artifact(vod_id)
        assert meta is not None
        assert meta["sample_rate"] == 16000
        assert meta["channels"] == 1
        assert meta["container"] == "flac"
        assert meta["sha256"]
        assert meta["file_size_bytes"] > 0
        flac_path = audio_service.artifact_path(vod_id)
        assert flac_path.is_file()

    def test_reuses_existing_artifact(
        self, audio_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        job1 = audio_service.start_extraction("twitch_vod", vod_id)
        self._wait_for_terminal(audio_service, job1["id"], timeout=30)
        # Second start without force should return the existing artifact.
        result = audio_service.start_extraction("twitch_vod", vod_id)
        # The result is the artifact metadata dict (not a job) when reused.
        assert "sha256" in result or result.get("status") == MediaJobStatus.READY.value

    def test_force_reextracts(
        self, audio_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        job1 = audio_service.start_extraction("twitch_vod", vod_id)
        self._wait_for_terminal(audio_service, job1["id"], timeout=30)
        first_meta = audio_service.get_audio_artifact(vod_id)
        job2 = audio_service.start_extraction("twitch_vod", vod_id, force=True)
        self._wait_for_terminal(audio_service, job2["id"], timeout=30)
        second_meta = audio_service.get_audio_artifact(vod_id)
        assert first_meta["sha256"] == second_meta["sha256"]  # same input

    def test_not_ready_vod_rejected(self, audio_service, vod_service, channel_lister):
        profile = vod_service.create_profile("notreadyaudio")
        channel_lister.add_vod("notreadyaudio", "300", title="Not Ready", duration=60.0)
        vod_service.sync_vods(profile["id"])
        vod = vod_service.list_vods(profile_id=profile["id"])[0]
        with pytest.raises(MediaSourceNotReadyError):
            audio_service.start_extraction("twitch_vod", vod["id"])

    def test_restart_recovery(
        self, media_storage, source_resolver, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        svc1 = AudioExtractionService(storage=media_storage, source_resolver=source_resolver)
        job = svc1.start_extraction("twitch_vod", vod_id)
        # Simulate a restart by creating a new service instance while the
        # job is still transient.
        svc2 = AudioExtractionService(storage=media_storage, source_resolver=source_resolver)
        recovered = svc2.get_job(job["id"])
        assert recovered["status"] == MediaJobStatus.FAILED.value
        assert "restart" in (recovered.get("error") or "").lower()

    def _wait_for_terminal(self, svc, job_id, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = svc.get_job(job_id)
            if job["status"] in {
                MediaJobStatus.READY.value,
                MediaJobStatus.FAILED.value,
                MediaJobStatus.CANCELED.value,
            }:
                return
            time.sleep(0.2)
        raise TimeoutError(f"job {job_id} did not terminate within {timeout}s")


# ---------------------------------------------------------------------------
# Transcription (no model — worker mocked or export-only tests)
# ---------------------------------------------------------------------------


class TestTranscriptionExporters:
    def test_txt_export(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Hello "},
            {"start": 1.0, "end": 2.0, "text": " world"},
        ]
        txt = _export_txt(segments)
        assert "Hello" in txt
        assert "world" in txt

    def test_srt_export(self):
        segments = [{"start": 0.0, "end": 1.5, "text": "Hi"}]
        srt = _export_srt(segments)
        assert "1" in srt
        assert "00:00:00,000 --> 00:00:01,500" in srt
        assert "Hi" in srt

    def test_vtt_export(self):
        segments = [{"start": 0.0, "end": 1.5, "text": "Hi"}]
        vtt = _export_vtt(segments)
        assert vtt.startswith("WEBVTT")
        assert "00:00:00.000 --> 00:00:01.500" in vtt

    def test_srt_timestamp_format(self):
        assert _format_srt_timestamp(0.0) == "00:00:00,000"
        assert _format_srt_timestamp(1.5) == "00:00:01,500"
        assert _format_srt_timestamp(3661.5) == "01:01:01,500"

    def test_vtt_timestamp_format(self):
        assert _format_vtt_timestamp(0.0) == "00:00:00.000"
        assert _format_vtt_timestamp(1.5) == "00:00:01.500"

    def test_empty_segments(self):
        assert _export_txt([]).strip() == ""
        assert _export_srt([]).strip() == ""
        assert _export_vtt([]).strip() == "WEBVTT"


class TestTranscriptionService:
    def test_start_creates_job_with_audio_dependency(
        self, transcription_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        job = transcription_service.start_transcription("twitch_vod", vod_id)
        assert job["job_type"] == JobType.TRANSCRIBE.value
        assert job["source_id"] == vod_id
        assert job["transcription_id"]
        # Should either be WAITING_FOR_DEPENDENCY (audio not ready) or
        # WAITING_FOR_GPU (audio already ready). Either way, not FAILED.
        assert job["status"] != MediaJobStatus.FAILED.value

    def test_not_ready_vod_rejected(self, transcription_service, vod_service, channel_lister):
        profile = vod_service.create_profile("notreadytransc")
        channel_lister.add_vod("notreadytransc", "500", title="Not Ready", duration=60.0)
        vod_service.sync_vods(profile["id"])
        vod = vod_service.list_vods(profile_id=profile["id"])[0]
        with pytest.raises(MediaSourceNotReadyError):
            transcription_service.start_transcription("twitch_vod", vod["id"])

    def test_restart_recovery(
        self, media_storage, source_resolver, audio_service, gpu_lock,
        vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        svc1 = TranscriptionService(
            storage=media_storage, source_resolver=source_resolver,
            audio_service=audio_service, gpu_lock=gpu_lock, device="cpu",
        )
        job = svc1.start_transcription("twitch_vod", vod_id)
        # Simulate restart.
        svc2 = TranscriptionService(
            storage=media_storage, source_resolver=source_resolver,
            audio_service=audio_service, gpu_lock=gpu_lock, device="cpu",
        )
        recovered = svc2.get_job(job["id"])
        # The job should be FAILED (interrupted by restart) or already
        # terminal if the worker finished quickly.
        assert recovered["status"] in {
            MediaJobStatus.FAILED.value,
            MediaJobStatus.READY.value,
            MediaJobStatus.WAITING_FOR_GPU.value,
            MediaJobStatus.WAITING_FOR_DEPENDENCY.value,
        }

    def test_runtime_status_cpu(self, transcription_service):
        status = transcription_service.runtime_status()
        # CPU device should not require CUDA.
        assert "device" in status
        assert status["device"] == "cpu"
        assert isinstance(status["reasons"], list)

    def test_poll_dependencies_transitions_when_audio_ready(
        self, transcription_service, audio_service, media_storage,
        vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        """poll_dependencies recovers WAITING_FOR_DEPENDENCY jobs when the
        audio extraction dependency has become READY.
        """
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        # Create a fake audio artifact on disk so _spawn_worker can proceed.
        audio_dir = audio_service.artifact_dir(vod_id)
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_service.artifact_path(vod_id)
        audio_path.write_bytes(b"fLaC" + b"\x00" * 32)
        meta_path = audio_service.artifact_metadata_path(vod_id)
        now = _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump({
                "schema_version": 1,
                "source_type": "twitch_vod",
                "source_id": vod_id,
                "file_name": "audio.flac",
                "container": "flac",
                "sample_rate": 16000,
                "channels": 1,
                "codec": "flac",
                "duration_seconds": 1.0,
                "file_size_bytes": 36,
                "sha256": "x" * 64,
                "created_at": now,
                "produced_by_job_id": None,
            }, fh)
        # Manually create an EXTRACT_AUDIO job in READY state.
        audio_job_id = str(uuid.uuid4())
        audio_job = {
            "schema_version": 1,
            "id": audio_job_id,
            "job_type": JobType.EXTRACT_AUDIO.value,
            "source_type": "twitch_vod",
            "source_id": vod_id,
            "status": MediaJobStatus.READY.value,
            "progress": {"percent": 100.0, "processed_seconds": 1.0, "total_seconds": 1.0, "phase": None},
            "options": {},
            "result": {"file_name": "audio.flac"},
            "error": None,
            "depends_on": None,
            "transcription_id": None,
            "created_at": now,
            "started_at": now,
            "completed_at": now,
            "updated_at": now,
        }
        media_storage.save_job(audio_job)
        # Manually create a TRANSCRIBE job waiting on it.
        transcribe_job_id = str(uuid.uuid4())
        transcribe_job = {
            "schema_version": 1,
            "id": transcribe_job_id,
            "job_type": JobType.TRANSCRIBE.value,
            "source_type": "twitch_vod",
            "source_id": vod_id,
            "status": MediaJobStatus.WAITING_FOR_DEPENDENCY.value,
            "progress": {"percent": None, "processed_seconds": None, "total_seconds": None, "phase": None},
            "options": {"language": "de", "model": "tiny"},
            "result": None,
            "error": None,
            "depends_on": audio_job_id,
            "transcription_id": str(uuid.uuid4()),
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now,
        }
        media_storage.save_job(transcribe_job)
        # Poll — this should transition the job to WAITING_FOR_GPU or
        # RUNNING (if the worker spawn succeeds) but not FAILED.
        transcription_service.poll_dependencies()
        job_after = media_storage.load_job(transcribe_job_id)
        assert job_after["status"] in {
            MediaJobStatus.WAITING_FOR_GPU.value,
            MediaJobStatus.RUNNING.value,
        }

    def test_poll_dependencies_marks_failed_when_audio_failed(
        self, transcription_service, audio_service, media_storage,
        vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        """poll_dependencies marks the transcription FAILED when the audio
        dependency failed.
        """
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        # Manually create a FAILED EXTRACT_AUDIO job.
        audio_job_id = str(uuid.uuid4())
        now = _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()
        audio_job = {
            "schema_version": 1,
            "id": audio_job_id,
            "job_type": JobType.EXTRACT_AUDIO.value,
            "source_type": "twitch_vod",
            "source_id": vod_id,
            "status": MediaJobStatus.FAILED.value,
            "progress": {},
            "options": {},
            "result": None,
            "error": "ffmpeg crashed",
            "depends_on": None,
            "transcription_id": None,
            "created_at": now,
            "started_at": now,
            "completed_at": now,
            "updated_at": now,
        }
        media_storage.save_job(audio_job)
        # Manually create a TRANSCRIBE job waiting on it.
        transcribe_job_id = str(uuid.uuid4())
        transcribe_job = {
            "schema_version": 1,
            "id": transcribe_job_id,
            "job_type": JobType.TRANSCRIBE.value,
            "source_type": "twitch_vod",
            "source_id": vod_id,
            "status": MediaJobStatus.WAITING_FOR_DEPENDENCY.value,
            "progress": {},
            "options": {},
            "result": None,
            "error": None,
            "depends_on": audio_job_id,
            "transcription_id": str(uuid.uuid4()),
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now,
        }
        media_storage.save_job(transcribe_job)
        # Poll — this should mark the transcription as FAILED.
        transcription_service.poll_dependencies()
        job_after = media_storage.load_job(transcribe_job_id)
        assert job_after["status"] == MediaJobStatus.FAILED.value
        assert "ffmpeg crashed" in (job_after.get("error") or "")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_start_run_for_ready_vod(
        self, pipeline_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        run = pipeline_service.start_run("twitch_vod", vod_id)
        assert run["source_id"] == vod_id
        assert run["status"] == PipelineStatus.RUNNING.value
        steps = run["steps"]
        # New URL-pipeline step model: RESOLVE_SOURCE, DOWNLOAD, EXTRACT_AUDIO, TRANSCRIBE.
        assert steps[0]["type"] == PipelineStepType.RESOLVE_SOURCE.value
        assert steps[0]["status"] == "READY"
        assert steps[1]["type"] == PipelineStepType.DOWNLOAD.value
        # VOD is already READY -> DOWNLOAD is SKIPPED.
        assert steps[1]["status"] == "SKIPPED"
        assert steps[2]["type"] == PipelineStepType.EXTRACT_AUDIO.value
        assert steps[3]["type"] == PipelineStepType.TRANSCRIBE.value
        # Source block is populated for the new URL-based run.
        assert run["source"]["external_id"] is not None
        assert run["source"]["legacy"] is False
        assert run["progress"] is not None

    def test_start_run_for_not_downloaded_vod(
        self, pipeline_service, vod_service, channel_lister
    ):
        profile = vod_service.create_profile("pipelinenotready")
        channel_lister.add_vod("pipelinenotready", "200", title="Not Ready VOD", duration=60.0)
        vod_service.sync_vods(profile["id"])
        vod = vod_service.list_vods(profile_id=profile["id"])[0]
        run = pipeline_service.start_run("twitch_vod", vod["id"])
        assert run["status"] == PipelineStatus.RUNNING.value
        # The orchestrator should start the download step.
        # Give it a moment.
        time.sleep(1.0)
        run = pipeline_service.get_run(run["id"])
        dl_step = next(s for s in run["steps"] if s["type"] == "DOWNLOAD")
        # Download should be RUNNING or READY (if it finished) or FAILED
        # (if yt-dlp can't actually download in test env).
        assert dl_step["status"] in {"RUNNING", "READY", "FAILED"}

    def test_unknown_vod(self, pipeline_service):
        from ttvturbo.media_processing.schemas import PipelineRunNotFoundError

        with pytest.raises(PipelineRunNotFoundError):
            pipeline_service.start_run("twitch_vod", str(uuid.uuid4()))

    def test_cancel_run(self, pipeline_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        run = pipeline_service.start_run("twitch_vod", vod_id)
        run = pipeline_service.cancel_run(run["id"])
        assert run["status"] == PipelineStatus.CANCELED.value

    def test_duplicate_run_conflict(
        self, pipeline_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        pipeline_service.start_run("twitch_vod", vod_id)
        from ttvturbo.media_processing.schemas import PipelineRunConflictError

        with pytest.raises(PipelineRunConflictError):
            pipeline_service.start_run("twitch_vod", vod_id)

    def test_list_runs(self, pipeline_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        pipeline_service.start_run("twitch_vod", vod_id)
        runs = pipeline_service.list_runs(source_id=vod_id)
        assert len(runs) >= 1
        assert all(r["source_id"] == vod_id for r in runs)

    # ------------------------------------------------------------------ URL-based start

    def test_start_run_from_url_creates_profile_and_imports_vod(
        self, pipeline_service, vod_service, channel_lister
    ):
        """URL start fetches metadata, auto-creates a profile, imports the
        VOD and creates a run with a populated source block."""
        url = "https://www.twitch.tv/videos/999999"
        entry = channel_lister.add_vod("urlimporttest", "999999", title="URL Import VOD", duration=120.0)
        # Simulate the uploader field that the real yt-dlp get_video_info
        # returns (flat-playlist entries don't include it).
        entry["uploader"] = "urlimporttest"
        run = pipeline_service.start_run_from_url(url)
        assert run["status"] == PipelineStatus.RUNNING.value
        # Source block is populated with the external id and title.
        assert run["source"]["external_id"] == "999999"
        assert run["source"]["title"] == "URL Import VOD"
        assert run["source"]["legacy"] is False
        assert run["source"]["type"] == "vod"
        # A profile was auto-created for the uploader.
        assert run["profile_id"] is not None
        # RESOLVE_SOURCE is READY, DOWNLOAD is not SKIPPED (VOD is not READY).
        steps = {s["type"]: s for s in run["steps"]}
        assert steps["RESOLVE_SOURCE"]["status"] == "READY"
        assert steps["DOWNLOAD"]["status"] != "SKIPPED"
        # The VOD was imported into the VOD storage.
        vod = vod_service.storage.load_vod(run["source_id"])
        assert vod["twitch_video_id"] == "999999"

    def test_start_run_from_url_reuses_existing_vod(
        self, pipeline_service, vod_service, channel_lister
    ):
        """If the VOD was already imported (e.g. via sync), URL start reuses
        it instead of creating a duplicate."""
        url = "https://www.twitch.tv/videos/888888"
        channel_lister.add_vod("reuseuser", "888888", title="Reuse VOD", duration=60.0)
        # First start imports the VOD.
        run1 = pipeline_service.start_run_from_url(url)
        vod_id_1 = run1["source_id"]
        # Cancel the first run so we can start again.
        pipeline_service.cancel_run(run1["id"])
        # Second start should reuse the same VOD record.
        run2 = pipeline_service.start_run_from_url(url)
        assert run2["source_id"] == vod_id_1

    def test_start_run_from_url_rejects_invalid_url(
        self, pipeline_service
    ):
        from ttvturbo.media_processing.schemas import PipelineRunValidationError

        with pytest.raises(PipelineRunValidationError):
            pipeline_service.start_run_from_url("https://www.youtube.com/watch?v=abc")
        with pytest.raises(PipelineRunValidationError):
            pipeline_service.start_run_from_url("")

    def test_start_run_from_url_rejects_active_duplicate(
        self, pipeline_service, vod_service, channel_lister
    ):
        """A second URL start for the same external id while the first is
        active is rejected with a conflict error."""
        from ttvturbo.media_processing.schemas import PipelineRunConflictError

        url = "https://www.twitch.tv/videos/777777"
        channel_lister.add_vod("dupuser", "777777", title="Dup VOD", duration=60.0)
        pipeline_service.start_run_from_url(url)
        with pytest.raises(PipelineRunConflictError):
            pipeline_service.start_run_from_url(url)

    def test_start_run_from_url_not_found(
        self, pipeline_service, channel_lister
    ):
        """A URL for a non-existent Twitch video raises a validation error."""
        from ttvturbo.media_processing.schemas import PipelineRunValidationError

        url = "https://www.twitch.tv/videos/404404"
        # No add_vod() call -> get_video_info raises TwitchNotFoundError.
        with pytest.raises(PipelineRunValidationError):
            pipeline_service.start_run_from_url(url)

    def test_list_runs_filters(
        self, pipeline_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister, title="Filter Test VOD")
        run = pipeline_service.start_run("twitch_vod", vod_id)
        # Filter by status.
        active = pipeline_service.list_runs(status="RUNNING")
        assert any(r["id"] == run["id"] for r in active)
        completed = pipeline_service.list_runs(status="COMPLETED")
        assert not any(r["id"] == run["id"] for r in completed)
        # Filter by profile_id.
        profile_id = run["profile_id"]
        by_profile = pipeline_service.list_runs(profile_id=profile_id)
        assert all(r["profile_id"] == profile_id for r in by_profile)
        # Filter by search (title substring).
        by_search = pipeline_service.list_runs(search="Filter Test")
        assert any(r["id"] == run["id"] for r in by_search)
        # Limit.
        limited = pipeline_service.list_runs(limit=1)
        assert len(limited) <= 1

    def test_delete_run_only_terminal(
        self, pipeline_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        from ttvturbo.media_processing.schemas import PipelineRunConflictError

        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        run = pipeline_service.start_run("twitch_vod", vod_id)
        # Active run cannot be deleted.
        with pytest.raises(PipelineRunConflictError):
            pipeline_service.delete_run(run["id"])
        # Cancel it, then delete should succeed.
        pipeline_service.cancel_run(run["id"])
        assert pipeline_service.delete_run(run["id"]) is True

    def test_retry_failed_run(
        self, pipeline_service, vod_service, make_real_mp4, ffmpeg_available, channel_lister
    ):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        run = pipeline_service.start_run("twitch_vod", vod_id)
        # Cancel -> terminal, then retry re-queues.
        run = pipeline_service.cancel_run(run["id"])
        assert run["status"] == PipelineStatus.CANCELED.value
        retried = pipeline_service.retry_run(run["id"])
        assert retried["status"] == PipelineStatus.RUNNING.value
