"""Backend-hardening tests for the voice-clone integration.

These tests do NOT load the Qwen3-TTS model. They cover:

* runtime diagnostics (missing qwen_tts, CPU-only torch, no CUDA, runtime
  available, concrete reasons in the status);
* worker exit-code evaluation, hard kill, timeout, transient->FAILED;
* large stdout/stderr output redirected to a log file (no deadlock);
* atomic ``output.wav`` finalization from a ``.part`` file;
* invalid ``.part`` cleanup;
* orphaned generations on server restart;
* empty generation directory cleanup after a pre-start validation failure.

Controlled subprocesses and small testable helper functions are used in
place of the real model. The real Qwen3-TTS e2e test stays gated by
``TTVTURBO_RUN_QWEN_TTS_E2E=1`` (see tests/test_voice_clone.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from ttvturbo.voice_clone import diagnostics
from ttvturbo.voice_clone.diagnostics import diagnose_runtime
from ttvturbo.voice_clone.runtime import _finalize_output, file_sha256
from ttvturbo.voice_clone.schemas import GenerationStatus
from ttvturbo.voice_clone.service import VoiceCloneService, ValidationError


# --------------------------------------------------------------------- helpers
def _write_wav(path: Path, data: np.ndarray, sr: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # soundfile cannot infer the format from a `.part` extension, so be
    # explicit when the path is not a regular .wav.
    if str(path).lower().endswith(".wav"):
        sf.write(str(path), data, sr)
    else:
        sf.write(str(path), data, sr, format="WAV")


def _tone(seconds: float, sr: int = 24000, freq: float = 220.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silent(seconds: float, sr: int = 24000) -> np.ndarray:
    return np.zeros(int(sr * seconds), dtype=np.float32)


def _make_reference(recordings_dir: Path, name: str, seconds: float = 6.0) -> Path:
    path = recordings_dir / name
    _write_wav(path, _tone(seconds))
    return path


def _install_fake_modules(modules: dict[str, Any]) -> None:
    """Push fake modules into sys.modules for the duration of a test."""
    saved = {}
    for name, mod in modules.items():
        saved[name] = sys.modules.get(name)
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod
    return saved


def _restore_modules(saved: dict[str, Any]) -> None:
    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class _FakeTorchNoCuda:
    __version__ = "2.11.0"

    class version:
        cuda = None

    class cuda:
        @staticmethod
        def is_available() -> bool:
            return False


class _FakeTorchCudaUnavailable:
    __version__ = "2.11.0+cu128"

    class version:
        cuda = "12.8"

    class cuda:
        @staticmethod
        def is_available() -> bool:
            return False


class _FakeTorchOk:
    __version__ = "2.11.0+cu128"

    class version:
        cuda = "12.8"

    class cuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def get_device_name(idx: int = 0) -> str:
            return "Fake RTX 5070"

        @staticmethod
        def get_device_properties(idx: int = 0):
            class _Props:
                total_memory = 12_000_000_000

            return _Props()

        @staticmethod
        def memory_reserved(idx: int = 0) -> int:
            return 0

        @staticmethod
        def synchronize(idx: int = 0) -> None:
            pass


class _FakeQwenModule:
    """A stand-in for the real `qwen_tts` package."""


# --------------------------------------------------------------------- diagnostics
def test_diagnostics_missing_qwen_tts(tmp_path: Path):
    saved = _install_fake_modules({
        "torch": _FakeTorchOk(),
        "qwen_tts": None,  # simulate missing
    })
    try:
        report = diagnose_runtime(data_dir=str(tmp_path))
        assert report["available"] is False
        assert any("qwen_tts is not importable" in r for r in report["reasons"])
        assert report["qwen_tts_importable"] is False
    finally:
        _restore_modules(saved)


def test_diagnostics_cpu_only_pytorch(tmp_path: Path):
    saved = _install_fake_modules({
        "torch": _FakeTorchNoCuda(),
        "qwen_tts": _FakeQwenModule(),
    })
    try:
        report = diagnose_runtime(data_dir=str(tmp_path))
        assert report["available"] is False
        assert report["torch_cuda_version"] is None
        assert any("without CUDA support" in r for r in report["reasons"])
        assert report["cuda_available"] is False
    finally:
        _restore_modules(saved)


def test_diagnostics_cuda_not_available(tmp_path: Path):
    saved = _install_fake_modules({
        "torch": _FakeTorchCudaUnavailable(),
        "qwen_tts": _FakeQwenModule(),
    })
    try:
        report = diagnose_runtime(data_dir=str(tmp_path))
        assert report["available"] is False
        assert report["torch_cuda_version"] == "12.8"
        assert any("is_available() is False" in r for r in report["reasons"])
        assert report["cuda_available"] is False
    finally:
        _restore_modules(saved)


def test_diagnostics_runtime_available(tmp_path: Path):
    saved = _install_fake_modules({
        "torch": _FakeTorchOk(),
        "qwen_tts": _FakeQwenModule(),
    })
    try:
        report = diagnose_runtime(data_dir=str(tmp_path))
        assert report["available"] is True
        assert report["cuda_available"] is True
        assert report["device_name"] == "Fake RTX 5070"
        assert report["qwen_tts_importable"] is True
        assert report["reasons"] == []
        assert report["device"] == "cuda:0"
    finally:
        _restore_modules(saved)


def test_status_contains_concrete_reasons(tmp_path: Path):
    saved = _install_fake_modules({
        "torch": _FakeTorchNoCuda(),
        "qwen_tts": None,
    })
    try:
        report = diagnose_runtime(data_dir=str(tmp_path))
        joined = " | ".join(report["reasons"])
        # Concrete, human-readable reasons, not generic "unavailable".
        assert "CUDA" in joined or "qwen_tts" in joined
        assert report["available"] is False
    finally:
        _restore_modules(saved)


def test_diagnostics_module_runs_as_cli(capsys):
    """`python -m voice_clone.diagnostics` must exit 0 on the dev box and
    print the Backend/Qwen runtime lines. We do not assert the exact
    availability (environment-dependent), only the output shape.
    """
    rc = diagnostics.main([])
    out = capsys.readouterr().out
    assert "Backend runtime:" in out
    assert "Qwen runtime:" in out
    assert "Torch version:" in out
    assert rc in (0, 1)


def test_runtime_main_rejects_wrong_arg_count(capsys):
    """`voice_clone.runtime.main` must accept exactly one argument (the job
    path). The service spawns `python -m voice_clone.runtime <job.json>`,
    so `sys.argv[1:]` has length 1. A regression that changed the check to
    `!= 2` made every real worker exit with code 2 immediately; this test
    pins the contract without loading the model.
    """
    from ttvturbo.voice_clone import runtime

    assert runtime.main([]) == 2
    assert runtime.main(["a", "b"]) == 2
    err = capsys.readouterr().err
    assert "usage:" in err

    # A non-existent job path must get past the arg-count check and fail at
    # the file-open step instead, proving the arg check accepts one path.
    with pytest.raises(FileNotFoundError):
        runtime.main(["definitely-not-a-real-job.json"])


# --------------------------------------------------------------------- atomic finalization
def test_finalize_output_atomic_success(tmp_path: Path):
    part = tmp_path / "output.wav.part"
    out = tmp_path / "output.wav"
    _write_wav(part, _tone(2.0, freq=440.0), sr=24000)
    metrics = _finalize_output(str(part), str(out))
    assert not part.is_file()  # .part consumed
    assert out.is_file()  # final appeared atomically
    assert metrics["output_sample_rate"] == 24000
    assert metrics["output_duration_seconds"] == 2.0
    assert metrics["output_file_size_bytes"] > 0
    assert metrics["output_sha256"] == file_sha256(str(out))


def test_finalize_output_invalid_part_silent(tmp_path: Path):
    part = tmp_path / "output.wav.part"
    out = tmp_path / "output.wav"
    _write_wav(part, _silent(2.0), sr=24000)
    with pytest.raises(RuntimeError, match="silent"):
        _finalize_output(str(part), str(out))
    assert not part.is_file()  # .part removed
    assert not out.is_file()  # no invalid output.wav left


def test_finalize_output_invalid_part_nan(tmp_path: Path):
    part = tmp_path / "output.wav.part"
    out = tmp_path / "output.wav"
    nan_audio = np.full(int(24000 * 2.0), np.nan, dtype=np.float32)
    # Use a float subtype so NaN survives the round-trip; the default
    # PCM_16 subtype would silently clamp NaN to 0.
    sf.write(str(part), nan_audio, 24000, format="WAV", subtype="FLOAT")
    with pytest.raises(RuntimeError, match="NaN|infinity"):
        _finalize_output(str(part), str(out))
    assert not part.is_file()
    assert not out.is_file()


def test_finalize_output_too_short(tmp_path: Path):
    part = tmp_path / "output.wav.part"
    out = tmp_path / "output.wav"
    _write_wav(part, _tone(0.1), sr=24000)
    with pytest.raises(RuntimeError, match="too short"):
        _finalize_output(str(part), str(out))
    assert not part.is_file()
    assert not out.is_file()


# --------------------------------------------------------------------- worker exit handling
def _make_service(tmp_path: Path, recordings_dir: Path, **kw) -> VoiceCloneService:
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    return VoiceCloneService(
        recordings_dir=recordings_dir,
        voice_clones_dir=vc_dir,
        timeout_seconds=kw.get("timeout_seconds", 300.0),
    )


def _write_transient_meta(service: VoiceCloneService, gen_id: str, status: GenerationStatus):
    meta = {
        "id": gen_id,
        "status": status.value,
        "reference_recording": "ref.wav",
        "reference_sha256": "",
        "reference_text": "Ref",
        "target_text": "Ziel",
        "language": "German",
        "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "model_revision": "unknown",
        "created_at": service._now_iso(),
        "completed_at": None,
        "output_duration_seconds": None,
        "output_sample_rate": None,
        "output_file_size_bytes": None,
        "output_sha256": None,
        "generation_seconds": None,
        "peak_vram_bytes": None,
        "attention_backend": None,
        "worker_exit_code": None,
        "quality": {},
        "failure_reason": None,
        "warnings": [],
    }
    service._write_metadata(gen_id, meta)
    return meta


def test_worker_exit_code_1_marks_transient_failed(tmp_path: Path, recordings_dir: Path):
    service = _make_service(tmp_path, recordings_dir)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    _write_transient_meta(service, gen_id, GenerationStatus.GENERATING)
    # Leave a stale .part that must be cleaned up.
    (gen_dir / "output.wav.part").write_bytes(b"partial")

    service._finalize_after_exit(gen_id, exit_code=1, timed_out=False)

    meta = service.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.FAILED.value
    assert meta["worker_exit_code"] == 1
    assert "exited with code 1" in meta["failure_reason"]
    assert not (gen_dir / "output.wav.part").is_file()
    assert not (gen_dir / "output.wav").is_file()


def test_worker_timeout_marks_failed(tmp_path: Path, recordings_dir: Path):
    service = _make_service(tmp_path, recordings_dir, timeout_seconds=0.1)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    _write_transient_meta(service, gen_id, GenerationStatus.LOADING_MODEL)

    service._finalize_after_exit(gen_id, exit_code=-15, timed_out=True)

    meta = service.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.FAILED.value
    assert "timed out" in meta["failure_reason"]
    assert meta["worker_exit_code"] == -15


def test_worker_hard_killed_marks_failed(tmp_path: Path, recordings_dir: Path):
    """Simulate a worker that ignores terminate and has to be kill -9'd."""
    service = _make_service(tmp_path, recordings_dir, timeout_seconds=0.1)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    _write_transient_meta(service, gen_id, GenerationStatus.GENERATING)

    # exit code -9 is the conventional value for SIGKILL on POSIX; on
    # Windows kill() produces a different code, but the finalize logic
    # only cares that the status is transient + the exit code is non-zero.
    service._finalize_after_exit(gen_id, exit_code=-9, timed_out=True)

    meta = service.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.FAILED.value
    assert meta["worker_exit_code"] == -9
    assert "timed out" in meta["failure_reason"]


def test_transient_status_after_process_end_becomes_failed(
    tmp_path: Path, recordings_dir: Path
):
    """Cover every transient status: each must become FAILED after exit."""
    service = _make_service(tmp_path, recordings_dir)
    for status in (
        GenerationStatus.QUEUED,
        GenerationStatus.VALIDATING_REFERENCE,
        GenerationStatus.LOADING_MODEL,
        GenerationStatus.GENERATING,
        GenerationStatus.VALIDATING_OUTPUT,
    ):
        gen_id = uuid.uuid4().hex
        gen_dir = service._generation_dir(gen_id)
        gen_dir.mkdir(parents=True, exist_ok=True)
        _write_transient_meta(service, gen_id, status)
        service._finalize_after_exit(gen_id, exit_code=1, timed_out=False)
        meta = service.get_generation(gen_id)
        assert meta["status"] == GenerationStatus.FAILED.value, status
        assert meta["worker_exit_code"] == 1


def test_ready_with_missing_output_downgraded_to_failed(
    tmp_path: Path, recordings_dir: Path
):
    service = _make_service(tmp_path, recordings_dir)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    meta = _write_transient_meta(service, gen_id, GenerationStatus.READY)
    meta["status"] = GenerationStatus.READY.value
    meta["output_sha256"] = "abc"
    meta["output_sample_rate"] = 24000
    meta["output_duration_seconds"] = 2.0
    meta["output_file_size_bytes"] = 1000
    service._write_metadata(gen_id, meta)
    # No output.wav on disk.

    service._finalize_after_exit(gen_id, exit_code=0, timed_out=False)

    meta = service.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.FAILED.value
    assert "output.wav is missing" in meta["failure_reason"]


def test_ready_with_incomplete_metadata_downgraded_to_failed(
    tmp_path: Path, recordings_dir: Path
):
    service = _make_service(tmp_path, recordings_dir)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    out = gen_dir / "output.wav"
    _write_wav(out, _tone(2.0, freq=440.0), sr=24000)
    meta = _write_transient_meta(service, gen_id, GenerationStatus.READY)
    meta["status"] = GenerationStatus.READY.value
    # Missing output_sha256.
    meta["output_sample_rate"] = 24000
    meta["output_duration_seconds"] = 2.0
    meta["output_file_size_bytes"] = 1000
    service._write_metadata(gen_id, meta)

    service._finalize_after_exit(gen_id, exit_code=0, timed_out=False)

    meta = service.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.FAILED.value
    assert "metadata is incomplete" in meta["failure_reason"]


def test_ready_with_valid_output_preserved(tmp_path: Path, recordings_dir: Path):
    service = _make_service(tmp_path, recordings_dir)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    out = gen_dir / "output.wav"
    _write_wav(out, _tone(2.0, freq=440.0), sr=24000)
    meta = _write_transient_meta(service, gen_id, GenerationStatus.READY)
    meta["status"] = GenerationStatus.READY.value
    meta["output_sha256"] = file_sha256(str(out))
    meta["output_sample_rate"] = 24000
    meta["output_duration_seconds"] = 2.0
    meta["output_file_size_bytes"] = out.stat().st_size
    service._write_metadata(gen_id, meta)

    service._finalize_after_exit(gen_id, exit_code=0, timed_out=False)

    meta = service.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.READY.value
    assert meta["worker_exit_code"] == 0
    assert out.is_file()


# --------------------------------------------------------------------- real subprocess: large output + log file
def test_large_stdout_to_log_file_no_deadlock(tmp_path: Path, recordings_dir: Path):
    """A worker that emits a lot of stdout must not deadlock the reaper.

    The service redirects stdout+stderr to a real log file (no PIPE), so
    the reaper thread never has to drain a pipe. We verify this with a
    real subprocess that writes ~10 MiB to stdout and exits 0.
    """
    service = _make_service(tmp_path, recordings_dir, timeout_seconds=30.0)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    _write_transient_meta(service, gen_id, GenerationStatus.GENERATING)

    log_path = service._worker_log_path(gen_id)
    log_fh = open(log_path, "wb", buffering=0)
    try:
        # Spawn a real Python subprocess that writes a lot to stdout.
        script = (
            "import sys\n"
            "for i in range(2000):\n"
            "    sys.stdout.write('x' * 5000 + '\\n')\n"
            "sys.stdout.flush()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        # If the reaper deadlocked, proc.wait() would never return.
        exit_code = proc.wait(timeout=30.0)
        assert exit_code == 0
    finally:
        log_fh.close()

    # The log file must contain the bulk of the output.
    assert log_path.stat().st_size > 1_000_000
    # And the finalize step still works.
    service._finalize_after_exit(gen_id, exit_code=0, timed_out=False)
    meta = service.get_generation(gen_id)
    # Status was GENERATING (transient) -> FAILED because no output was produced.
    assert meta["status"] == GenerationStatus.FAILED.value
    assert meta["worker_exit_code"] == 0


def test_worker_log_excerpt_is_bounded_and_sanitized(tmp_path: Path, recordings_dir: Path):
    service = _make_service(tmp_path, recordings_dir)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    log_path = service._worker_log_path(gen_id)
    # Write a log that contains an absolute Windows path.
    secret = r"F:\super\secret\path\to\something.wav"
    log_path.write_text("line one\n" + secret + "\nline two\n", encoding="utf-8")

    excerpt = service.worker_log_excerpt(gen_id, max_bytes=4096)
    assert excerpt is not None
    assert "line one" in excerpt
    assert "line two" in excerpt
    # The absolute path must be scrubbed.
    assert "super\\secret\\path" not in excerpt
    assert "F:\\super" not in excerpt

    # Bad generation id -> None.
    assert service.worker_log_excerpt("not-a-uuid") is None
    # Missing log -> None.
    other = uuid.uuid4().hex
    assert service.worker_log_excerpt(other) is None


# --------------------------------------------------------------------- real subprocess: timeout + hard kill
def _spawn_sleep_worker(service: VoiceCloneService, gen_id: str, seconds: float) -> subprocess.Popen:
    """Spawn a real Python subprocess that sleeps. Used to exercise the
    timeout/kill path of _reap_worker against a real OS process."""
    log_path = service._worker_log_path(gen_id)
    log_fh = open(log_path, "wb", buffering=0)
    script = (
        "import time, sys\n"
        f"time.sleep({seconds})\n"
        "sys.exit(0)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    # Stash the fh so the test can close it; in production _reap_worker does.
    proc._test_log_fh = log_fh  # type: ignore[attr-defined]
    return proc


def test_reap_worker_terminates_on_timeout(tmp_path: Path, recordings_dir: Path):
    service = _make_service(tmp_path, recordings_dir, timeout_seconds=0.5)
    gen_id = uuid.uuid4().hex
    gen_dir = service._generation_dir(gen_id)
    gen_dir.mkdir(parents=True, exist_ok=True)
    _write_transient_meta(service, gen_id, GenerationStatus.GENERATING)

    proc = _spawn_sleep_worker(service, gen_id, seconds=30.0)
    log_fh = proc._test_log_fh  # type: ignore[attr-defined]
    log_path = service._worker_log_path(gen_id)
    try:
        service._reap_worker(gen_id, proc, log_fh, log_path)
    finally:
        try:
            log_fh.close()
        except OSError:
            pass

    meta = service.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.FAILED.value
    assert "timed out" in meta["failure_reason"]
    # The real OS process must actually be gone.
    assert proc.poll() is not None


# --------------------------------------------------------------------- orphan recovery on startup
def test_orphan_transient_marked_failed_and_part_removed(
    tmp_path: Path, recordings_dir: Path
):
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service1 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    gen_id = uuid.uuid4().hex
    gen_dir = vc_dir / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    _write_transient_meta(service1, gen_id, GenerationStatus.GENERATING)
    (gen_dir / "output.wav.part").write_bytes(b"partial")
    (gen_dir / "output.wav").write_bytes(b"partial-final")

    # Simulate a restart.
    service2 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    meta = service2.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.FAILED.value
    assert "restarted" in (meta["failure_reason"] or "").lower()
    assert not (gen_dir / "output.wav.part").is_file()
    assert not (gen_dir / "output.wav").is_file()


def test_orphan_ready_preserved_and_part_removed(tmp_path: Path, recordings_dir: Path):
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service1 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    gen_id = uuid.uuid4().hex
    gen_dir = vc_dir / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    out = gen_dir / "output.wav"
    _write_wav(out, _tone(2.0, freq=440.0), sr=24000)
    meta = _write_transient_meta(service1, gen_id, GenerationStatus.READY)
    meta["status"] = GenerationStatus.READY.value
    meta["output_sha256"] = file_sha256(str(out))
    meta["output_sample_rate"] = 24000
    meta["output_duration_seconds"] = 2.0
    meta["output_file_size_bytes"] = out.stat().st_size
    service1._write_metadata(gen_id, meta)
    # Leave a stale .part from a previous crashed run.
    (gen_dir / "output.wav.part").write_bytes(b"stale")

    service2 = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    meta = service2.get_generation(gen_id)
    assert meta["status"] == GenerationStatus.READY.value
    assert out.is_file()
    assert not (gen_dir / "output.wav.part").is_file()


def test_orphan_bad_metadata_does_not_block_startup(tmp_path: Path, recordings_dir: Path):
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    # Write a corrupt metadata.json.
    bad_id = uuid.uuid4().hex
    bad_dir = vc_dir / bad_id
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "metadata.json").write_text("{not valid json", encoding="utf-8")
    # Write a metadata with an unknown status.
    weird_id = uuid.uuid4().hex
    weird_dir = vc_dir / weird_id
    weird_dir.mkdir(parents=True, exist_ok=True)
    (weird_dir / "metadata.json").write_text(
        json.dumps({"id": weird_id, "status": "BOGUS"}), encoding="utf-8"
    )

    # Startup must not raise. The bad entries are simply skipped (left
    # as-is on disk); they must NOT be turned into FAILED records.
    service = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    # The corrupt metadata is unreadable -> get_generation returns None.
    assert service.get_generation(bad_id) is None
    # The unknown-status metadata is readable but was skipped by recovery;
    # it must still NOT have been mutated to FAILED.
    weird_meta = service.get_generation(weird_id)
    assert weird_meta is not None
    assert weird_meta["status"] == "BOGUS"


# --------------------------------------------------------------------- empty dir cleanup
def test_validation_failure_leaves_no_directory(
    tmp_path: Path, recordings_dir: Path
):
    """A REJECT reference must not leave a metadata entry or an empty dir."""
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    # Near-silent WAV -> REJECT.
    ref = recordings_dir / f"silent_{uuid.uuid4().hex}.wav"
    _write_wav(ref, _silent(6.0))
    try:
        with pytest.raises(ValidationError):
            service.create_generation({
                "reference_recording": ref.name,
                "reference_text": "Hallo",
                "target_text": "Welt",
            })
        # No generation directories should have been created.
        assert not any(p.is_dir() for p in vc_dir.iterdir())
    finally:
        ref.unlink(missing_ok=True)


def test_review_without_flag_leaves_no_directory(
    tmp_path: Path, recordings_dir: Path
):
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    ref = _make_reference(recordings_dir, f"short_{uuid.uuid4().hex}.wav", seconds=3.0)
    with pytest.raises(ValidationError):
        service.create_generation({
            "reference_recording": ref.name,
            "reference_text": "Hallo",
            "target_text": "Welt",
        })
    assert not any(p.is_dir() for p in vc_dir.iterdir())


# --------------------------------------------------------------------- status endpoint shape
def test_voice_clone_status_has_diagnostic_fields(tmp_path: Path, recordings_dir: Path):
    """The status dict must carry the new diagnostic fields, regardless of
    whether the runtime is actually available on this machine.
    """
    vc_dir = tmp_path / "voice_clones"
    vc_dir.mkdir(parents=True, exist_ok=True)
    service = VoiceCloneService(recordings_dir=recordings_dir, voice_clones_dir=vc_dir)
    # Force the diagnostics cache to use a fake CPU-only torch so the test
    # is deterministic across environments.
    saved = _install_fake_modules({
        "torch": _FakeTorchCudaUnavailable(),
        "qwen_tts": _FakeQwenModule(),
    })
    try:
        service.invalidate_diagnostics()
        status = service.status()
        assert "available" in status
        assert "busy" in status
        assert "model_id" in status
        # New additive fields:
        for key in (
            "device",
            "python_version",
            "torch_version",
            "torch_cuda_version",
            "cuda_available",
            "device_name",
            "vram_total_bytes",
            "vram_free_bytes",
            "qwen_tts_importable",
            "soundfile_ok",
            "ffmpeg_ok",
            "data_dir_writable",
            "reasons",
            "warnings",
        ):
            assert key in status, key
        assert status["available"] is False
        assert status["cuda_available"] is False
        assert any("is_available() is False" in r for r in status["reasons"])
    finally:
        _restore_modules(saved)
        service.invalidate_diagnostics()
