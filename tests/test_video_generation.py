"""Tests for the Video Generation backend capability.

Covers the required scenarios from the spec:

* capability unavailable;
* valid job (TEXT_TO_VIDEO and IMAGE_TO_VIDEO);
* invalid resolution / aspect ratio;
* missing input image;
* cancel;
* retry;
* recovery;
* settings-wiring;
* artifact metadata (model id, revision, prompt, seed, effective
  options, duration, resolution, fps);
* no real model loading in standard tests.

These tests never load a real generation model.  They inject a
synchronous fake worker runner that produces a real, FFprobe-verifiable
MP4 via ffmpeg so the source-resolver, library registration and
artifact-metadata code paths exercise the real implementation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ttvturbo.settings import Settings
from ttvturbo.video_generation import (
    ASPECT_RATIOS,
    RESOLUTIONS_BY_ASPECT_RATIO,
    SUPPORTED_GENERATION_TYPES,
    UnavailableVideoGenerationAdapter,
    VideoGenerationArtifact,
    VideoGenerationConflictError,
    VideoGenerationJobStatus,
    VideoGenerationNotFoundError,
    VideoGenerationService,
    VideoGenerationStorage,
    VideoGenerationUnavailableError,
    VideoGenerationValidationError,
    make_job_record,
    resolution_for_aspect_ratio,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vg_settings(vod_data_dir: Path) -> Settings:
    s = Settings(data_root=vod_data_dir)
    s.video_generation_t2v_model_id = "THUDM/CogVideoX-2b-test"
    s.video_generation_i2v_model_id = "THUDM/CogVideoX1.5-5B-I2V-test"
    s.video_generation_device = "cpu"
    s.video_generation_dtype = "float32"
    s.video_generation_fps = 8
    s.video_generation_max_duration_seconds = 10.0
    s.video_generation_max_prompt_length = 1000
    return s


@pytest.fixture()
def vg_storage(vg_settings: Settings) -> VideoGenerationStorage:
    return VideoGenerationStorage(vg_settings.paths().video_generation)


@pytest.fixture()
def library_service(vod_data_dir: Path):
    from ttvturbo.library import LibraryService, LibraryStorage

    return LibraryService(LibraryStorage(vod_data_dir / "library"))


@pytest.fixture()
def source_resolver(vod_service, library_service):
    from ttvturbo.media_processing import MediaSourceResolver

    return MediaSourceResolver(
        vod_service.storage,
        library_service=library_service,
    )


@pytest.fixture()
def gpu_lock(vod_data_dir: Path):
    from ttvturbo.media_processing import GpuLock

    return GpuLock(vod_data_dir)


class _AvailableAdapter:
    """Test adapter that reports available."""

    def available(self) -> bool:
        return True

    def capabilities(self) -> dict:
        return {"available": True, "generation_types": ["TEXT_TO_VIDEO", "IMAGE_TO_VIDEO"]}


@pytest.fixture()
def make_real_mp4():
    """Generate a tiny, FFprobe-verifiable MP4 with ffmpeg."""
    def _make(path: Path, duration_seconds: float = 1.0, width: int = 160, height: int = 120, fps: int = 8) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            pytest.skip("ffmpeg not available")
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration_seconds}:size={width}x{height}:rate={fps}",
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


@pytest.fixture()
def make_real_png():
    """Generate a tiny real PNG image with ffmpeg."""
    def _make(path: Path, width: int = 64, height: int = 64) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            pytest.skip("ffmpeg not available")
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi",
            "-i", f"color=c=red:s={width}x{height}:d=1",
            "-frames:v", "1",
            str(path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0 or not path.is_file():
            pytest.skip("could not generate test png: " + proc.stderr.decode("utf-8", errors="replace")[-300:])
    return _make


def _make_fake_worker_runner(make_real_mp4, model_revision: str = "v1.0-test"):
    """Return a synchronous worker runner that produces a real MP4.

    It mirrors the real worker contract: updates job.json, writes
    output.mp4 + result.json.  No model is loaded.
    """
    def _runner(worker_job: dict, job_dir: Path) -> None:
        from ttvturbo.storage_utils import atomic_write_json, now_iso

        with open(job_dir / "job.json", "r", encoding="utf-8-sig") as fh:
            job = json.load(fh)
        job["status"] = VideoGenerationJobStatus.RUNNING
        job["started_at"] = now_iso()
        job["current_stage"] = "generate"
        job["progress"] = 30.0
        atomic_write_json(job_dir / "job.json", job, Exception, kind="vg-job")

        resolution = worker_job.get("resolution") or [720, 480]
        fps = int(worker_job.get("fps") or 8)
        duration = float(worker_job.get("duration_seconds") or 1.0)
        # Generate a real mp4 at the requested resolution.
        out_path = job_dir / "output.mp4"
        make_real_mp4(out_path, duration_seconds=max(0.4, duration / 5.0), width=int(resolution[0]), height=int(resolution[1]), fps=fps)

        num_frames = int((worker_job.get("effective_options") or {}).get("num_frames", 49))
        effective_duration = round((num_frames - 1) / fps, 3) if fps > 0 else duration
        result = {
            "success": True,
            "model_id": worker_job.get("model_id") or "test-model",
            "model_revision": model_revision,
            "prompt": worker_job.get("prompt") or "",
            "seed": int(worker_job.get("seed") or 0),
            "duration_seconds": float(effective_duration),
            "resolution": [int(resolution[0]), int(resolution[1])],
            "fps": int(fps),
            "file_name": "output.mp4",
            "file_size_bytes": int(out_path.stat().st_size),
            "effective_options": worker_job.get("effective_options") or {},
            "error": None,
        }
        atomic_write_json(job_dir / "result.json", result, Exception, kind="vg-result")

        job["status"] = VideoGenerationJobStatus.COMPLETED
        job["progress"] = 100.0
        job["current_stage"] = None
        job["completed_at"] = now_iso()
        atomic_write_json(job_dir / "job.json", job, Exception, kind="vg-job")
    return _runner


@pytest.fixture()
def vg_service(
    vg_storage, source_resolver, vg_settings, library_service, gpu_lock, make_real_mp4,
):
    svc = VideoGenerationService(
        storage=vg_storage,
        source_resolver=source_resolver,
        settings=vg_settings,
        gpu_lock=gpu_lock,
        library_service=library_service,
        worker_python="python",
        adapter=_AvailableAdapter(),
        worker_runner=_make_fake_worker_runner(make_real_mp4),
    )
    # The test environment has no diffusers/torch installed; stub the
    # availability checks so the service reports available for tests
    # that exercise the full pipeline.
    svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
    svc._check_cuda_available = lambda: True  # noqa: SLF001
    svc._check_worker_module = lambda: True  # noqa: SLF001
    return svc


@pytest.fixture()
def app(vg_settings):
    from ttvturbo.app_factory import create_app

    return create_app(settings=vg_settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image_library_item(library_service, make_real_png, file_name="source.png") -> str:
    meta = library_service.create_upload_item(file_name=file_name, title="I2V source")
    item_id = meta["id"]
    dest = library_service.storage.source_file_path(item_id, "mp4")
    # Use the original extension for the image.
    dest = dest.parent / file_name
    make_real_png(dest, width=64, height=64)
    meta["file_name"] = file_name
    meta["container"] = "png"
    meta["file_size_bytes"] = dest.stat().st_size
    library_service.storage.save_item(meta)
    return item_id


# ---------------------------------------------------------------------------
# Schema / validation unit tests
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_supported_generation_types(self):
        assert "TEXT_TO_VIDEO" in SUPPORTED_GENERATION_TYPES
        assert "IMAGE_TO_VIDEO" in SUPPORTED_GENERATION_TYPES

    def test_aspect_ratios_whitelist(self):
        assert ASPECT_RATIOS == frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"})

    def test_resolution_for_aspect_ratio(self):
        assert resolution_for_aspect_ratio("16:9") == (720, 480)
        assert resolution_for_aspect_ratio("9:16") == (480, 720)

    def test_resolution_for_unknown_ratio_raises(self):
        with pytest.raises(VideoGenerationValidationError):
            resolution_for_aspect_ratio("2:1")

    def test_artifact_rejects_non_whitelisted_resolution(self):
        with pytest.raises(Exception):
            VideoGenerationArtifact(
                id=str(uuid.uuid4()),
                job_id=str(uuid.uuid4()),
                generation_type="TEXT_TO_VIDEO",
                model_id="m",
                prompt="p",
                seed=1,
                duration_seconds=1.0,
                resolution=[999, 999],
                fps=8,
                library_item_id=str(uuid.uuid4()),
                file_name="output.mp4",
                file_size_bytes=10,
                created_at="2024-01-01T00:00:00+00:00",
            )

    def test_artifact_rejects_unknown_generation_type(self):
        with pytest.raises(Exception):
            VideoGenerationArtifact(
                id=str(uuid.uuid4()),
                job_id=str(uuid.uuid4()),
                generation_type="MAGIC",
                model_id="m",
                prompt="p",
                seed=1,
                duration_seconds=1.0,
                resolution=[720, 480],
                fps=8,
                library_item_id=str(uuid.uuid4()),
                file_name="output.mp4",
                file_size_bytes=10,
                created_at="2024-01-01T00:00:00+00:00",
            )

    def test_make_job_record_queued(self):
        job = make_job_record(
            job_id=str(uuid.uuid4()),
            generation_type="TEXT_TO_VIDEO",
            prompt="hello",
            source_image_asset_id=None,
            duration_seconds=5.0,
            aspect_ratio="16:9",
            seed=42,
            options={},
            effective_options={},
            resolution=[720, 480],
            fps=8,
            model_id="m",
            created_at="2024-01-01T00:00:00+00:00",
        )
        assert job["status"] == VideoGenerationJobStatus.QUEUED
        assert job["seed"] == 42
        assert job["type"] == "TEXT_TO_VIDEO"


# ---------------------------------------------------------------------------
# Capability unavailable
# ---------------------------------------------------------------------------


class TestCapabilityUnavailable:
    def test_unavailable_when_no_model_configured(self, vod_data_dir, gpu_lock, library_service, source_resolver):
        s = Settings(data_root=vod_data_dir)
        # No model ids configured.
        assert s.video_generation_t2v_model_id == ""
        assert s.video_generation_i2v_model_id == ""
        svc = VideoGenerationService(
            storage=VideoGenerationStorage(s.paths().video_generation),
            source_resolver=source_resolver,
            settings=s,
            gpu_lock=gpu_lock,
            library_service=library_service,
            adapter=UnavailableVideoGenerationAdapter(),
        )
        status = svc.runtime_status()
        assert status["available"] is False
        assert "no video-generation model configured" in status["reasons"]

    def test_unavailable_adapter_reports_false(self):
        adapter = UnavailableVideoGenerationAdapter()
        assert adapter.available() is False
        caps = adapter.capabilities()
        assert caps["available"] is False
        assert caps["generation_types"] == []

    def test_capabilities_only_advertises_configured_types(self, vg_service):
        caps = vg_service.capabilities()
        # Both models are configured in vg_settings.
        assert "TEXT_TO_VIDEO" in caps["generation_types"]
        assert "IMAGE_TO_VIDEO" in caps["generation_types"]
        assert set(caps["resolutions"].keys()) == ASPECT_RATIOS

    def test_start_job_unavailable_raises(self, vod_data_dir, gpu_lock, library_service, source_resolver):
        s = Settings(data_root=vod_data_dir)
        svc = VideoGenerationService(
            storage=VideoGenerationStorage(s.paths().video_generation),
            source_resolver=source_resolver,
            settings=s,
            gpu_lock=gpu_lock,
            library_service=library_service,
            adapter=UnavailableVideoGenerationAdapter(),
        )
        with pytest.raises(VideoGenerationUnavailableError):
            svc.start_job("TEXT_TO_VIDEO", "a prompt")


# ---------------------------------------------------------------------------
# Valid job
# ---------------------------------------------------------------------------


class TestValidJob:
    def test_text_to_video_completes_and_registers_artifact(self, vg_service):
        job = vg_service.start_job(
            "TEXT_TO_VIDEO",
            "a cat playing piano",
            duration_seconds=5.0,
            aspect_ratio="16:9",
            seed=123,
        )
        assert job["status"] == VideoGenerationJobStatus.COMPLETED
        assert job["output_artifact_id"] is not None
        assert job["library_item_id"] is not None
        assert job["seed"] == 123
        assert job["type"] == "TEXT_TO_VIDEO"

        artifact = vg_service.get_artifact(job["output_artifact_id"])
        assert artifact["generation_type"] == "TEXT_TO_VIDEO"
        assert artifact["prompt"] == "a cat playing piano"
        assert artifact["seed"] == 123
        assert artifact["resolution"] == [720, 480]
        assert artifact["library_item_id"] == job["library_item_id"]

    def test_image_to_video_completes(
        self, vg_service, library_service, make_real_png,
    ):
        item_id = _make_image_library_item(library_service, make_real_png)
        job = vg_service.start_job(
            "IMAGE_TO_VIDEO",
            "make the image dance",
            source_image_asset_id=item_id,
            duration_seconds=5.0,
            aspect_ratio="9:16",
            seed=7,
        )
        assert job["status"] == VideoGenerationJobStatus.COMPLETED
        assert job["source_image_asset_id"] == item_id
        artifact = vg_service.get_artifact(job["output_artifact_id"])
        assert artifact["generation_type"] == "IMAGE_TO_VIDEO"
        assert artifact["source_image_asset_id"] == item_id
        assert artifact["resolution"] == [480, 720]

    def test_generated_video_moved_into_library(
        self, vg_service, library_service,
    ):
        job = vg_service.start_job("TEXT_TO_VIDEO", "prompt", seed=1)
        item_id = job["library_item_id"]
        # The library item should own a real mp4 file.
        path = library_service.item_file_path(item_id)
        assert path.is_file()
        assert path.stat().st_size > 0
        # The original output.mp4 in the job dir should be gone (moved).
        assert not vg_service.storage.output_path(job["id"]).is_file()

    def test_seed_null_allowed(self, vg_service):
        job = vg_service.start_job("TEXT_TO_VIDEO", "prompt", seed=None)
        assert job["status"] == VideoGenerationJobStatus.COMPLETED
        assert job["seed"] is None
        artifact = vg_service.get_artifact(job["output_artifact_id"])
        # The worker records 0 when seed is null.
        assert artifact["seed"] == 0


# ---------------------------------------------------------------------------
# Invalid resolution / aspect ratio
# ---------------------------------------------------------------------------


class TestInvalidResolution:
    def test_unknown_aspect_ratio_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("TEXT_TO_VIDEO", "prompt", aspect_ratio="2:1")

    def test_duration_too_long_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("TEXT_TO_VIDEO", "prompt", duration_seconds=999.0)

    def test_duration_zero_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("TEXT_TO_VIDEO", "prompt", duration_seconds=0.0)

    def test_prompt_too_long_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("TEXT_TO_VIDEO", "x" * (vg_service.settings.video_generation_max_prompt_length + 1))

    def test_empty_prompt_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("TEXT_TO_VIDEO", "")

    def test_unknown_generation_type_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("MAGIC", "prompt")

    def test_text_to_video_with_image_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job(
                "TEXT_TO_VIDEO", "prompt", source_image_asset_id=str(uuid.uuid4()),
            )

    def test_negative_seed_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("TEXT_TO_VIDEO", "prompt", seed=-1)

    def test_invalid_options_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("TEXT_TO_VIDEO", "prompt", options={"num_frames": 1})


# ---------------------------------------------------------------------------
# Missing input image
# ---------------------------------------------------------------------------


class TestMissingInputImage:
    def test_i2v_without_asset_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job("IMAGE_TO_VIDEO", "prompt")

    def test_i2v_with_nonexistent_asset_rejected(self, vg_service):
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job(
                "IMAGE_TO_VIDEO", "prompt", source_image_asset_id=str(uuid.uuid4()),
            )

    def test_i2v_with_non_image_asset_rejected(
        self, vg_service, library_service, make_real_mp4,
    ):
        # Create a library item whose file is an mp4, not an image.
        meta = library_service.create_upload_item(file_name="clip.mp4", title="not an image")
        item_id = meta["id"]
        dest = library_service.storage.source_file_path(item_id, "mp4")
        make_real_mp4(dest, duration_seconds=0.5)
        meta["file_size_bytes"] = dest.stat().st_size
        library_service.storage.save_item(meta)
        with pytest.raises(VideoGenerationValidationError):
            vg_service.start_job(
                "IMAGE_TO_VIDEO", "prompt", source_image_asset_id=item_id,
            )


# ---------------------------------------------------------------------------
# Cancel / Retry / Recovery
# ---------------------------------------------------------------------------


class TestCancelRetryRecovery:
    def test_cancel_non_cancellable_rejected(self, vg_service):
        job = vg_service.start_job("TEXT_TO_VIDEO", "prompt")
        assert job["status"] == VideoGenerationJobStatus.COMPLETED
        with pytest.raises(VideoGenerationConflictError):
            vg_service.cancel_job(job["id"])

    def test_cancel_active_job(
        self, vg_storage, source_resolver, vg_settings, library_service, gpu_lock,
    ):
        # Build a service whose runner blocks until a flag is set, so we
        # can observe a RUNNING job and cancel it.
        import threading as _t

        proceed = _t.Event()

        def blocking_runner(worker_job, job_dir):
            from ttvturbo.storage_utils import atomic_write_json, now_iso
            with open(job_dir / "job.json", "r", encoding="utf-8-sig") as fh:
                job = json.load(fh)
            job["status"] = VideoGenerationJobStatus.RUNNING
            job["started_at"] = now_iso()
            atomic_write_json(job_dir / "job.json", job, Exception, kind="vg-job")
            proceed.wait(timeout=5.0)
            # Simulate the worker observing the cancel and stopping.
            job = json.load(open(job_dir / "job.json", encoding="utf-8-sig"))
            if job.get("status") != VideoGenerationJobStatus.CANCELED:
                job["status"] = VideoGenerationJobStatus.COMPLETED
                job["completed_at"] = now_iso()
                atomic_write_json(job_dir / "job.json", job, Exception, kind="vg-job")

        svc = VideoGenerationService(
            storage=vg_storage,
            source_resolver=source_resolver,
            settings=vg_settings,
            gpu_lock=gpu_lock,
            library_service=library_service,
            worker_python="python",
            adapter=_AvailableAdapter(),
            worker_runner=blocking_runner,
        )
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_cuda_available = lambda: True  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001

        # Start the job in a thread (the runner blocks).
        result: dict = {}
        def _start():
            result["job"] = svc.start_job("TEXT_TO_VIDEO", "prompt")
        t = _t.Thread(target=_start, daemon=True)
        t.start()
        # Wait for the job to appear in storage as RUNNING.
        import time as _time
        deadline = _time.monotonic() + 5.0
        job_id = None
        while _time.monotonic() < deadline:
            jobs = [j for j in vg_storage.iter_jobs() if j.get("status") == VideoGenerationJobStatus.RUNNING]
            if jobs:
                job_id = jobs[0]["id"]
                break
            _time.sleep(0.05)
        assert job_id is not None, "job never reached RUNNING"
        canceled = svc.cancel_job(job_id)
        assert canceled["status"] == VideoGenerationJobStatus.CANCELED
        proceed.set()
        t.join(timeout=5.0)
        # The final stored status is CANCELED (cancel wins).
        final = vg_storage.load_job(job_id)
        assert final["status"] == VideoGenerationJobStatus.CANCELED

    def test_retry_failed_job(
        self, vg_storage, source_resolver, vg_settings, library_service, gpu_lock, make_real_mp4,
    ):
        # First service uses a failing runner.
        def failing_runner(worker_job, job_dir):
            from ttvturbo.storage_utils import atomic_write_json, now_iso
            with open(job_dir / "job.json", "r", encoding="utf-8-sig") as fh:
                job = json.load(fh)
            job["status"] = VideoGenerationJobStatus.FAILED
            job["error"] = {"code": "VG_WORKER", "message": "boom", "retryable": False}
            job["completed_at"] = now_iso()
            atomic_write_json(job_dir / "job.json", job, Exception, kind="vg-job")

        svc = VideoGenerationService(
            storage=vg_storage,
            source_resolver=source_resolver,
            settings=vg_settings,
            gpu_lock=gpu_lock,
            library_service=library_service,
            worker_python="python",
            adapter=_AvailableAdapter(),
            worker_runner=failing_runner,
        )
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_cuda_available = lambda: True  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001
        job = svc.start_job("TEXT_TO_VIDEO", "prompt", seed=5)
        assert job["status"] == VideoGenerationJobStatus.FAILED
        assert job["output_artifact_id"] is None

        # Swap in a successful runner and retry.
        svc._worker_runner = _make_fake_worker_runner(make_real_mp4)  # noqa: SLF001
        retried = svc.retry_job(job["id"])
        assert retried["status"] == VideoGenerationJobStatus.COMPLETED
        assert retried["output_artifact_id"] is not None

    def test_retry_active_rejected(self, vg_service, vg_storage):
        from ttvturbo.storage_utils import now_iso
        job_id = str(uuid.uuid4())
        job = make_job_record(
            job_id=job_id, generation_type="TEXT_TO_VIDEO", prompt="p",
            source_image_asset_id=None, duration_seconds=5.0, aspect_ratio="16:9",
            seed=1, options={}, effective_options={}, resolution=[720, 480], fps=8,
            model_id="m", created_at=now_iso(),
        )
        job["status"] = VideoGenerationJobStatus.RUNNING
        vg_storage.save_job(job)
        with pytest.raises(VideoGenerationConflictError):
            vg_service.retry_job(job_id)

    def test_retry_completed_rejected(self, vg_service):
        job = vg_service.start_job("TEXT_TO_VIDEO", "prompt")
        assert job["status"] == VideoGenerationJobStatus.COMPLETED
        with pytest.raises(VideoGenerationConflictError):
            vg_service.retry_job(job["id"])

    def test_recovery_marks_active_jobs_failed(
        self, vg_storage, source_resolver, vg_settings, library_service, gpu_lock, make_real_mp4,
    ):
        from ttvturbo.storage_utils import now_iso
        # Persist a RUNNING job with no live worker (simulating a restart).
        job_id = str(uuid.uuid4())
        job = make_job_record(
            job_id=job_id, generation_type="TEXT_TO_VIDEO", prompt="p",
            source_image_asset_id=None, duration_seconds=5.0, aspect_ratio="16:9",
            seed=1, options={}, effective_options={}, resolution=[720, 480], fps=8,
            model_id="m", created_at=now_iso(),
        )
        job["status"] = VideoGenerationJobStatus.RUNNING
        vg_storage.save_job(job)

        # Construct a fresh service -> _recover_on_startup runs.
        svc = VideoGenerationService(
            storage=vg_storage,
            source_resolver=source_resolver,
            settings=vg_settings,
            gpu_lock=gpu_lock,
            library_service=library_service,
            worker_python="python",
            adapter=_AvailableAdapter(),
            worker_runner=_make_fake_worker_runner(make_real_mp4),
        )
        recovered = vg_storage.load_job(job_id)
        assert recovered["status"] == VideoGenerationJobStatus.FAILED
        assert recovered["error"]["code"] == "VG_RECOVERY"
        assert recovered["error"]["retryable"] is True


# ---------------------------------------------------------------------------
# Artifact metadata
# ---------------------------------------------------------------------------


class TestArtifactMetadata:
    def test_artifact_carries_full_metadata(self, vg_service):
        job = vg_service.start_job(
            "TEXT_TO_VIDEO",
            "a cinematic shot of a city at night",
            duration_seconds=5.0,
            aspect_ratio="16:9",
            seed=4242,
            options={"num_frames": 49, "guidance_scale": 7.5, "num_inference_steps": 50},
        )
        assert job["status"] == VideoGenerationJobStatus.COMPLETED
        artifact = vg_service.get_artifact(job["output_artifact_id"])

        # Model id + revision.
        assert artifact["model_id"] == vg_service.settings.video_generation_t2v_model_id
        assert artifact["model_revision"] == "v1.0-test"
        # Prompt + seed (reproducibility documentation).
        assert artifact["prompt"] == "a cinematic shot of a city at night"
        assert artifact["seed"] == 4242
        # Effective options (whitelisted + bounded).
        eff = artifact["effective_options"]
        assert eff["num_frames"] == 49
        assert eff["guidance_scale"] == 7.5
        assert eff["num_inference_steps"] == 50
        # Duration + resolution + fps.
        assert artifact["duration_seconds"] > 0
        assert artifact["resolution"] == [720, 480]
        assert artifact["fps"] == 8
        # Library linkage.
        assert artifact["library_item_id"] == job["library_item_id"]
        assert artifact["file_name"] == "source.mp4"
        assert artifact["file_size_bytes"] > 0
        assert artifact["container"] == "mp4"

    def test_failed_job_has_no_artifact(
        self, vg_storage, source_resolver, vg_settings, library_service, gpu_lock,
    ):
        def failing_runner(worker_job, job_dir):
            from ttvturbo.storage_utils import atomic_write_json, now_iso
            with open(job_dir / "job.json", "r", encoding="utf-8-sig") as fh:
                job = json.load(fh)
            job["status"] = VideoGenerationJobStatus.FAILED
            job["error"] = {"code": "VG_WORKER", "message": "boom", "retryable": False}
            job["completed_at"] = now_iso()
            atomic_write_json(job_dir / "job.json", job, Exception, kind="vg-job")

        svc = VideoGenerationService(
            storage=vg_storage,
            source_resolver=source_resolver,
            settings=vg_settings,
            gpu_lock=gpu_lock,
            library_service=library_service,
            worker_python="python",
            adapter=_AvailableAdapter(),
            worker_runner=failing_runner,
        )
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_cuda_available = lambda: True  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001
        job = svc.start_job("TEXT_TO_VIDEO", "prompt")
        assert job["status"] == VideoGenerationJobStatus.FAILED
        assert job["output_artifact_id"] is None
        assert list(vg_storage.iter_artifacts()) == []

    def test_library_item_back_reference(self, vg_service, library_service):
        job = vg_service.start_job("TEXT_TO_VIDEO", "prompt")
        meta = library_service.get_item(job["library_item_id"])
        artifacts = meta.get("artifacts") or []
        assert any(a["artifact_type"] == "video_generation" for a in artifacts)
        assert meta.get("generated") is True
        gen = meta.get("generation") or {}
        assert gen["job_id"] == job["id"]
        assert gen["artifact_id"] == job["output_artifact_id"]


# ---------------------------------------------------------------------------
# Settings wiring
# ---------------------------------------------------------------------------


class TestSettingsWiring:
    def test_default_settings_empty_models(self):
        s = Settings(data_root=Path("/tmp/ttv-vg-defaults"))
        assert s.video_generation_t2v_model_id == ""
        assert s.video_generation_i2v_model_id == ""
        assert s.video_generation_fps == 8
        assert s.video_generation_max_duration_seconds == 10.0
        assert s.video_generation_max_prompt_length == 1000
        assert s.video_generation_max_concurrent == 1
        assert s.video_generation_device == "cuda"
        assert s.video_generation_dtype == "bfloat16"

    def test_settings_reach_service(self, vg_settings, vg_storage, source_resolver, gpu_lock, library_service):
        vg_settings.video_generation_t2v_model_id = "custom/t2v"
        svc = VideoGenerationService(
            storage=vg_storage,
            source_resolver=source_resolver,
            settings=vg_settings,
            gpu_lock=gpu_lock,
            library_service=library_service,
        )
        assert svc.settings.video_generation_t2v_model_id == "custom/t2v"
        assert svc.settings.paths().video_generation.name == "video_generation"

    def test_data_paths_includes_video_generation(self, vg_settings):
        paths = vg_settings.paths()
        assert paths.video_generation == paths.data_root / "video_generation"

    def test_gpu_lock_accepts_video_generation_owner(self, gpu_lock):
        from ttvturbo.media_processing.gpu_lock import (
            OWNER_VIDEO_GENERATION,
            VALID_OWNER_TYPES,
        )
        assert OWNER_VIDEO_GENERATION in VALID_OWNER_TYPES
        gpu_lock.acquire(OWNER_VIDEO_GENERATION, "test-job")
        owner = gpu_lock.current_owner()
        assert owner is not None
        assert owner["owner_type"] == "video_generation"
        gpu_lock.release(OWNER_VIDEO_GENERATION, "test-job")
        assert gpu_lock.current_owner() is None


# ---------------------------------------------------------------------------
# No real model loading in standard tests
# ---------------------------------------------------------------------------


class TestNoModelLoading:
    def test_diffusers_not_imported_at_app_start(self, vg_settings, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.startswith("diffusers"):
                raise ImportError(f"blocked: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guarded_import)
        from ttvturbo.app_factory import create_app

        app = create_app(settings=vg_settings)
        # The app object exists and the router is registered even though
        # diffusers is unimportable.
        paths: set[str] = set()

        def walk(routes):
            for route in routes:
                if hasattr(route, "original_router"):
                    walk(route.original_router.routes)
                    continue
                if hasattr(route, "routes"):
                    walk(route.routes)
                    continue
                if hasattr(route, "path"):
                    paths.add(route.path)

        walk(app.routes)
        assert "/api/video-generation/capabilities" in paths

    def test_worker_module_importable_without_gpu_deps(self):
        # The worker module must import without diffusers/torch installed.
        import importlib

        mod = importlib.import_module("ttvturbo.video_generation.worker")
        assert hasattr(mod, "run_worker")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestAPI:
    def test_capabilities_endpoint(self, client):
        resp = client.get("/api/video-generation/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "generation_types" in data
        assert "aspect_ratios" in data
        assert "resolutions" in data
        assert "max_duration_seconds" in data

    def test_status_endpoint(self, client):
        resp = client.get("/api/video-generation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert "reasons" in data

    def test_list_jobs_empty(self, client):
        resp = client.get("/api/video-generation/jobs")
        assert resp.status_code == 200
        assert resp.json() == {"jobs": []}

    def test_start_job_unavailable_returns_503(self, vod_data_dir):
        # App with no model configured -> 503.
        s = Settings(data_root=vod_data_dir)
        from ttvturbo.app_factory import create_app

        app = create_app(settings=s)
        with TestClient(app) as c:
            resp = c.post(
                "/api/video-generation/jobs",
                json={"type": "TEXT_TO_VIDEO", "prompt": "hi"},
            )
            assert resp.status_code == 503

    def test_start_job_invalid_resolution_returns_400(self, client):
        resp = client.post(
            "/api/video-generation/jobs",
            json={"type": "TEXT_TO_VIDEO", "prompt": "hi", "aspect_ratio": "2:1"},
        )
        assert resp.status_code == 400

    def test_start_job_missing_input_image_returns_400(self, client):
        resp = client.post(
            "/api/video-generation/jobs",
            json={"type": "IMAGE_TO_VIDEO", "prompt": "hi"},
        )
        assert resp.status_code == 400

    def test_get_job_not_found_returns_404(self, client):
        resp = client.get(f"/api/video-generation/jobs/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_cancel_job_not_found_returns_404(self, client):
        resp = client.post(f"/api/video-generation/jobs/{uuid.uuid4()}/cancel")
        assert resp.status_code == 404

    def test_retry_job_not_found_returns_404(self, client):
        resp = client.post(f"/api/video-generation/jobs/{uuid.uuid4()}/retry")
        assert resp.status_code == 404

    def test_get_artifact_not_found_returns_404(self, client):
        resp = client.get(f"/api/video-generation/artifacts/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_full_api_flow_text_to_video(
        self, vg_settings, library_service, make_real_png, make_real_mp4,
    ):
        # Wire the app with the fake worker runner via service overrides.
        from ttvturbo.app_factory import create_app, ServiceOverrides

        storage = VideoGenerationStorage(vg_settings.paths().video_generation)
        svc = VideoGenerationService(
            storage=storage,
            source_resolver=None,
            settings=vg_settings,
            gpu_lock=None,
            library_service=library_service,
            worker_python="python",
            adapter=_AvailableAdapter(),
            worker_runner=_make_fake_worker_runner(make_real_mp4),
        )
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_cuda_available = lambda: True  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001
        # The service needs a source resolver for I2V only; T2V does not
        # use it, so None is fine for this flow.

        overrides = ServiceOverrides(video_generation_service=svc)
        app = create_app(settings=vg_settings, overrides=overrides)
        with TestClient(app) as c:
            resp = c.post(
                "/api/video-generation/jobs",
                json={
                    "type": "TEXT_TO_VIDEO",
                    "prompt": "a sunset over mountains",
                    "duration_seconds": 5.0,
                    "aspect_ratio": "16:9",
                    "seed": 99,
                },
            )
            assert resp.status_code == 201
            job = resp.json()
            assert job["status"] == VideoGenerationJobStatus.COMPLETED
            job_id = job["id"]

            # GET job.
            resp = c.get(f"/api/video-generation/jobs/{job_id}")
            assert resp.status_code == 200
            assert resp.json()["id"] == job_id

            # GET artifact.
            resp = c.get(f"/api/video-generation/artifacts/{job['output_artifact_id']}")
            assert resp.status_code == 200
            assert resp.json()["seed"] == 99

            # List jobs.
            resp = c.get("/api/video-generation/jobs")
            assert resp.status_code == 200
            assert any(j["id"] == job_id for j in resp.json()["jobs"])

    def test_full_api_flow_image_to_video(
        self, vg_settings, library_service, make_real_png, make_real_mp4, vod_service,
    ):
        from ttvturbo.app_factory import create_app, ServiceOverrides
        from ttvturbo.media_processing import MediaSourceResolver

        storage = VideoGenerationStorage(vg_settings.paths().video_generation)
        resolver = MediaSourceResolver(vod_service.storage, library_service=library_service)
        from ttvturbo.media_processing import GpuLock

        gpu_lock = GpuLock(vg_settings.paths().data_root)
        svc = VideoGenerationService(
            storage=storage,
            source_resolver=resolver,
            settings=vg_settings,
            gpu_lock=gpu_lock,
            library_service=library_service,
            worker_python="python",
            adapter=_AvailableAdapter(),
            worker_runner=_make_fake_worker_runner(make_real_mp4),
        )
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_cuda_available = lambda: True  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001

        item_id = _make_image_library_item(library_service, make_real_png)
        overrides = ServiceOverrides(video_generation_service=svc)
        app = create_app(settings=vg_settings, overrides=overrides)
        with TestClient(app) as c:
            resp = c.post(
                "/api/video-generation/jobs",
                json={
                    "type": "IMAGE_TO_VIDEO",
                    "prompt": "animate the picture",
                    "source_image_asset_id": item_id,
                    "aspect_ratio": "1:1",
                    "seed": 3,
                },
            )
            assert resp.status_code == 201
            job = resp.json()
            assert job["status"] == VideoGenerationJobStatus.COMPLETED
            assert job["source_image_asset_id"] == item_id
            assert job["resolution"] == [480, 480]


# ---------------------------------------------------------------------------
# Architecture / no-duplicate-routes
# ---------------------------------------------------------------------------


class TestArchitecture:
    def test_no_duplicate_routes(self, tmp_path):
        from ttvturbo.app_factory import create_app

        app = create_app(settings=Settings(data_root=tmp_path / "vg_routes"))
        seen: dict[tuple[str, str], int] = {}

        def walk(routes):
            for route in routes:
                if hasattr(route, "original_router"):
                    walk(route.original_router.routes)
                    continue
                if hasattr(route, "routes"):
                    walk(route.routes)
                    continue
                if hasattr(route, "methods") and hasattr(route, "path"):
                    for method in sorted(route.methods or []):
                        if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
                            key = (method, route.path)
                            seen[key] = seen.get(key, 0) + 1

        walk(app.routes)
        duplicates = {k: v for k, v in seen.items() if v > 1}
        assert not duplicates, f"duplicate routes: {duplicates}"

    def test_video_generation_routes_registered(self, tmp_path):
        from ttvturbo.app_factory import create_app

        app = create_app(settings=Settings(data_root=tmp_path / "vg_routes"))
        paths: set[str] = set()

        def walk(routes):
            for route in routes:
                if hasattr(route, "original_router"):
                    walk(route.original_router.routes)
                    continue
                if hasattr(route, "routes"):
                    walk(route.routes)
                    continue
                if hasattr(route, "path"):
                    paths.add(route.path)

        walk(app.routes)
        assert "/api/video-generation/capabilities" in paths
        assert "/api/video-generation/jobs" in paths
        assert "/api/video-generation/jobs/{job_id}/cancel" in paths
        assert "/api/video-generation/jobs/{job_id}/retry" in paths
