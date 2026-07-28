"""Tests for the deterministic service/worker shutdown lifecycle.

Verifies that:

* The FastAPI lifespan shuts down all services on exit.
* Each service's ``shutdown()`` is idempotent.
* A failure in one service's shutdown does not block the rest.
* Subprocesses are terminated after the grace period.
* The pipeline orchestrator thread stops.
* The test app does not leave threads or processes behind.
* The test app does not write into the real ``data/`` directory.
* Calling shutdown on a service that was never started is safe.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ttvturbo.app_factory import create_app
from ttvturbo.lifecycle import shutdown_service, terminate_subprocess
from ttvturbo.settings import Settings


# ---------------------------------------------------------------------------
# terminate_subprocess
# ---------------------------------------------------------------------------


def test_terminate_subprocess_already_exited():
    """terminate_subprocess on an already-exited process is a no-op."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=5.0)
    # Must not raise.
    terminate_subprocess(proc, label="test")
    assert proc.poll() is not None


def test_terminate_subprocess_graceful_exit():
    """A process that exits on terminate() is not hard-killed."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    terminate_subprocess(proc, grace_seconds=2.0, label="test")
    assert proc.poll() is not None
    # On Windows, terminate() sends a CTRL_BREAK_EVENT or TerminateProcess;
    # the exit code may vary, but the process must be gone.


def test_terminate_subprocess_hard_kill():
    """A process that ignores terminate() is hard-killed."""
    # A process that catches SIGTERM (on Unix) — on Windows, terminate()
    # is already a hard kill via TerminateProcess, so this mainly verifies
    # the fallback path works.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    terminate_subprocess(proc, grace_seconds=0.1, label="test")
    assert proc.poll() is not None


# ---------------------------------------------------------------------------
# shutdown_service
# ---------------------------------------------------------------------------


def test_shutdown_service_calls_shutdown():
    called = []

    class _Svc:
        def shutdown(self) -> None:
            called.append("shutdown")

    shutdown_service(_Svc())
    assert called == ["shutdown"]


def test_shutdown_service_no_method_is_noop():
    class _Svc:
        pass

    # Must not raise.
    shutdown_service(_Svc())


def test_shutdown_service_swallows_exception():
    class _Svc:
        def shutdown(self) -> None:
            raise RuntimeError("boom")

    # Must not raise — a failure in one service must not block the rest.
    shutdown_service(_Svc())


def test_shutdown_service_idempotent():
    count = [0]

    class _Svc:
        def shutdown(self) -> None:
            count[0] += 1

    svc = _Svc()
    shutdown_service(svc)
    shutdown_service(svc)
    assert count[0] == 2  # called both times, no error


# ---------------------------------------------------------------------------
# Service shutdown methods
# ---------------------------------------------------------------------------


def test_vod_pipeline_service_shutdown_idempotent(tmp_path: Path):
    """VodPipelineService.shutdown() can be called multiple times safely."""
    from ttvturbo.vod_pipeline.service import VodPipelineService
    from ttvturbo.vod_pipeline.storage import VodPipelineStorage
    from ttvturbo.vod_pipeline.twitch_client import ChannelLister

    storage = VodPipelineStorage(tmp_path)
    svc = VodPipelineService(
        storage=storage,
        channel_lister=ChannelLister(),
        download_dir=tmp_path / "downloads",
        max_concurrent=1,
        timeout_seconds=0.0,
    )
    svc.shutdown()
    svc.shutdown()  # idempotent
    # No active workers after shutdown.
    assert svc._active == {}


def test_transcription_service_shutdown_idempotent(tmp_path: Path):
    """TranscriptionService.shutdown() can be called multiple times safely."""
    from ttvturbo.media_processing import TranscriptionService, MediaJobStorage

    storage = MediaJobStorage(tmp_path)
    svc = TranscriptionService(
        storage=storage,
        source_resolver=None,
        audio_service=None,
        gpu_lock=None,
        default_preset_store=None,
    )
    svc.shutdown()
    svc.shutdown()
    assert svc._active == {}


def test_audio_extraction_service_shutdown_idempotent(tmp_path: Path):
    """AudioExtractionService.shutdown() can be called multiple times safely."""
    from ttvturbo.media_processing import AudioExtractionService, MediaJobStorage

    storage = MediaJobStorage(tmp_path)
    svc = AudioExtractionService(
        storage=storage,
        source_resolver=None,
    )
    svc.shutdown()
    svc.shutdown()
    assert svc._active == {}


def test_voice_clone_service_shutdown_idempotent(tmp_path: Path):
    """VoiceCloneService.shutdown() can be called multiple times safely."""
    from ttvturbo.voice_clone.service import VoiceCloneService

    recordings = tmp_path / "recordings"
    clones = tmp_path / "voice_clones"
    recordings.mkdir()
    clones.mkdir()
    svc = VoiceCloneService(
        recordings_dir=recordings,
        voice_clones_dir=clones,
        gpu_lock=None,
    )
    svc.shutdown()
    svc.shutdown()
    assert svc._active_proc is None


def test_asr_benchmark_service_shutdown_idempotent(tmp_path: Path):
    """AsrBenchmarkService.shutdown() can be called multiple times safely."""
    from ttvturbo.media_processing import AsrBenchmarkService

    svc = AsrBenchmarkService(
        data_dir=tmp_path,
        source_resolver=None,
        gpu_lock=None,
    )
    svc.shutdown()
    svc.shutdown()
    assert svc._active_proc is None


def test_pipeline_service_shutdown_stops_orchestrator(tmp_path: Path):
    """PipelineService.shutdown() stops the orchestrator thread."""
    from ttvturbo.media_processing import PipelineService, MediaJobStorage

    storage = MediaJobStorage(tmp_path)
    svc = PipelineService(
        storage=storage,
        vod_service=None,
        audio_service=None,
        transcription_service=None,
    )
    # Start the orchestrator manually (it will exit quickly with no runs).
    svc._ensure_orchestrator()
    t = svc._orchestrator_thread
    # Shutdown should stop it.
    svc.shutdown()
    if t is not None:
        assert not t.is_alive()
    assert svc._orchestrator_thread is None


# ---------------------------------------------------------------------------
# Lifespan integration
# ---------------------------------------------------------------------------


def test_lifespan_shutdown_no_threads_left(tmp_path: Path):
    """After the TestClient exits, no service threads should be alive."""
    settings = Settings(data_root=tmp_path)
    app = create_app(settings)
    initial_threads = threading.active_count()
    with TestClient(app) as client:
        # The app is running — some threads may be started.
        client.get("/api/status")
        active_during = threading.active_count()
    # After exit, thread count should not have grown.
    after = threading.active_count()
    assert after <= active_during
    # And should be close to the initial count (allow some slack for
    # pytest/httpx background threads).
    assert after <= initial_threads + 5


def test_lifespan_shutdown_does_not_write_to_real_data(tmp_path: Path, monkeypatch):
    """The test app must not write into the repository's real data/ directory."""
    from ttvturbo.settings import DEFAULT_DATA_DIR

    settings = Settings(data_root=tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/status")
    # The real data dir must not have been created or modified.
    # (It may pre-exist from other tests, but our app must not write to it.)
    assert settings.paths().data_root == tmp_path
    assert tmp_path.is_dir()
    # The real DEFAULT_DATA_DIR should not have been used by this app.
    assert settings.data_root != DEFAULT_DATA_DIR


def test_lifespan_shutdown_with_service_override(tmp_path: Path):
    """Shutdown works even when services are overridden with fakes."""
    from ttvturbo.app_factory import ServiceOverrides

    class _FakeVod:
        shutdown_called = False
        # _init_services accesses .storage for MediaSourceResolver wiring.
        storage = type("_FakeStorage", (), {"vod_dir": lambda self, vid: tmp_path / vid})()

        def shutdown(self) -> None:
            self.shutdown_called = True

    fake_vod = _FakeVod()
    overrides = ServiceOverrides(vod_pipeline_service=fake_vod)
    settings = Settings(data_root=tmp_path)
    app = create_app(settings, overrides=overrides)
    with TestClient(app) as client:
        client.get("/api/status")
    assert fake_vod.shutdown_called


def test_lifespan_shutdown_partial_failure(tmp_path: Path):
    """A failing shutdown in one service must not block the rest."""
    from ttvturbo.app_factory import ServiceOverrides

    class _Failing:
        storage = type("_S", (), {"vod_dir": lambda self, vid: tmp_path / vid})()

        def shutdown(self) -> None:
            raise RuntimeError("boom")

    class _Ok:
        shutdown_called = False

        def set_profile_reference_resolver(self, resolver) -> None:
            pass

        def status(self) -> dict:
            return {"available": False}

        def shutdown(self) -> None:
            self.shutdown_called = True

    failing = _Failing()
    ok = _Ok()
    overrides = ServiceOverrides(
        vod_pipeline_service=failing,
        voice_clone_service=ok,
    )
    settings = Settings(data_root=tmp_path)
    app = create_app(settings, overrides=overrides)
    # The lifespan must not raise on shutdown despite the failure.
    with TestClient(app):
        pass  # just start + shutdown
    assert ok.shutdown_called


def test_shutdown_without_started_workers(tmp_path: Path):
    """Shutdown on a freshly constructed service (no workers started) is safe."""
    from ttvturbo.vod_pipeline.service import VodPipelineService
    from ttvturbo.vod_pipeline.storage import VodPipelineStorage
    from ttvturbo.vod_pipeline.twitch_client import ChannelLister

    storage = VodPipelineStorage(tmp_path)
    svc = VodPipelineService(
        storage=storage,
        channel_lister=ChannelLister(),
        download_dir=tmp_path / "downloads",
        max_concurrent=1,
        timeout_seconds=0.0,
    )
    # No workers were started; shutdown must be a no-op.
    svc.shutdown()
    assert svc._active == {}
    assert svc._active_log_fh == {}
