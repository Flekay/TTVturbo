"""ASR benchmark service: run multiple presets on the same clip sequentially.

A benchmark is a single source (a twitch clip / library item / upload)
transcribed by several presets, one after another. The results are
persisted under ``asr_benchmarks/{benchmark_id}/`` inside the configured
data directory::

    asr_benchmarks/
      {benchmark_id}/
        benchmark.json          <- the benchmark record + runs
        runs/
          {preset_id}.json      <- one run per preset

Why sequential, not parallel:
  * the box has a single 12 GB GPU;
  * Large v3 and Large v3 Turbo must never be loaded simultaneously;
  * the existing GPU lock is exclusive;
  * Qwen3-TTS voice-clone must never run alongside a Whisper model.

A run is executed in an isolated worker subprocess
(:mod:`media_processing.asr_benchmark_worker`) so the FastAPI process
stays responsive and a crash never leaks a model into the next run. The
worker acquires the GPU lock, loads the model, transcribes, writes the
run JSON, releases the lock and exits. When two consecutive runs use the
*same* model the worker may reuse the loaded model for the second run,
but only inside the same subprocess and only if VRAM stays stable.

The service is intentionally minimal: no scheduler, no queue, no
cluster. One benchmark at a time, one run at a time.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .asr_diagnostics import (
    VadDiagnosis,
    compute_vad_regions,
    flag_hallucinations,
    flag_missing_speech,
)
from .asr_metrics import compute_metrics, hypothesis_text, rank_runs
from .asr_presets import (
    AsrPreset,
    AsrPresetError,
    AsrPresetNotFoundError,
    BUILTIN_PRESETS,
    check_preset_compatibility,
    get_preset,
    is_production_eligible,
)
from .gpu_lock import GpuLock
from .schemas import MediaSourceError, MediaSourceNotFoundError
from .sources import MediaSourceResolver

logger = logging.getLogger("ttvturbo.media_processing.asr_benchmark")

SCHEMA_VERSION = 1
BENCHMARKS_SUBDIR = "asr_benchmarks"
RUNS_SUBDIR = "runs"
BENCHMARK_FILENAME = "benchmark.json"

# Status values.
STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_READY = "READY"
STATUS_PARTIALLY_FAILED = "PARTIALLY_FAILED"
STATUS_FAILED = "FAILED"
STATUS_CANCELED = "CANCELED"

ALL_STATUSES = (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_READY,
    STATUS_PARTIALLY_FAILED,
    STATUS_FAILED,
    STATUS_CANCELED,
)
ACTIVE_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})
TERMINAL_STATUSES = frozenset({STATUS_READY, STATUS_PARTIALLY_FAILED, STATUS_FAILED, STATUS_CANCELED})

# Guardrails.
MAX_HOTWORDS_LEN = 500
MAX_REFERENCE_LEN = 5000


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


class AsrBenchmarkError(Exception):
    """Benchmark-level validation or state error."""


class AsrBenchmarkNotFoundError(AsrBenchmarkError):
    """A benchmark with the given id does not exist."""


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


class AsrBenchmarkService:
    """Owns benchmark records, run execution and persistence."""

    def __init__(
        self,
        data_dir: Path,
        source_resolver: MediaSourceResolver,
        gpu_lock: GpuLock,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / BENCHMARKS_SUBDIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_resolver = source_resolver
        self.gpu_lock = gpu_lock

        self._lock = threading.Lock()
        self._active_proc: Optional[subprocess.Popen] = None
        self._active_benchmark_id: Optional[str] = None
        self._cancel_event = threading.Event()

        self._recover_on_startup()

    # ------------------------------------------------------------------ paths
    def _benchmark_dir(self, benchmark_id: str, *, raise_not_found: bool = False) -> Path:
        try:
            uuid.UUID(benchmark_id)
        except (ValueError, AttributeError, TypeError):
            if raise_not_found:
                raise AsrBenchmarkNotFoundError(f"benchmark not found: {benchmark_id!r}")
            raise AsrBenchmarkError(f"invalid benchmark id: {benchmark_id!r}")
        return self.root / benchmark_id

    def _benchmark_path(self, benchmark_id: str) -> Path:
        return self._benchmark_dir(benchmark_id, raise_not_found=True) / BENCHMARK_FILENAME

    def _runs_dir(self, benchmark_id: str) -> Path:
        return self._benchmark_dir(benchmark_id) / RUNS_SUBDIR

    def _run_path(self, benchmark_id: str, preset_id: str) -> Path:
        # preset_id is constrained to the built-in set; sanitise anyway.
        if not preset_id or not isinstance(preset_id, str) or "/" in preset_id or "\\" in preset_id:
            raise AsrBenchmarkError(f"invalid preset id: {preset_id!r}")
        return self._runs_dir(benchmark_id) / f"{preset_id}.json"

    # ------------------------------------------------------------------ listing
    def list_benchmarks(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return out
        for entry in entries:
            if not entry.is_dir():
                continue
            payload = _read_json(entry / BENCHMARK_FILENAME)
            if payload is not None:
                out.append(payload)
        out.sort(key=lambda b: b.get("created_at", ""), reverse=True)
        return out

    def get_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        payload = _read_json(self._benchmark_path(benchmark_id))
        if payload is None:
            raise AsrBenchmarkNotFoundError(f"benchmark not found: {benchmark_id}")
        return payload

    # ------------------------------------------------------------------ create
    def create_benchmark(
        self,
        source_type: str,
        source_id: str,
        preset_ids: list[str],
        reference_text: Optional[str] = None,
        hotwords: Optional[str] = None,
    ) -> dict[str, Any]:
        # Validate source exists and is ready.
        try:
            self.source_resolver.resolve(source_type, source_id)
        except MediaSourceNotFoundError as exc:
            raise AsrBenchmarkError(str(exc)) from exc
        except MediaSourceError as exc:
            raise AsrBenchmarkError(str(exc)) from exc

        if not preset_ids:
            raise AsrBenchmarkError("at least one preset is required")
        # Validate every preset id is known.
        for pid in preset_ids:
            try:
                get_preset(pid)
            except AsrPresetNotFoundError as exc:
                raise AsrBenchmarkError(str(exc)) from exc

        # Guardrails on user input.
        ref = (reference_text or "").strip()
        if len(ref) > MAX_REFERENCE_LEN:
            raise AsrBenchmarkError(
                f"reference_text too long (max {MAX_REFERENCE_LEN} chars)"
            )
        hw = (hotwords or "").strip()
        if len(hw) > MAX_HOTWORDS_LEN:
            raise AsrBenchmarkError(
                f"hotwords too long (max {MAX_HOTWORDS_LEN} chars)"
            )

        benchmark_id = _new_uuid()
        now = _now_iso()
        # De-duplicate while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for pid in preset_ids:
            if pid not in seen:
                seen.add(pid)
                ordered.append(pid)

        payload = {
            "schema_version": SCHEMA_VERSION,
            "id": benchmark_id,
            "source_type": source_type,
            "source_id": source_id,
            "source_duration_seconds": None,
            "reference_text": ref or None,
            "hotwords": hw or None,
            "selected_presets": ordered,
            "status": STATUS_QUEUED,
            "created_at": now,
            "completed_at": None,
            "runs": [],
        }
        _atomic_write_json(self._benchmark_path(benchmark_id), payload)
        return payload

    # ------------------------------------------------------------------ run
    def start(self, benchmark_id: str) -> dict[str, Any]:
        """Start executing the benchmark runs sequentially in a subprocess."""
        with self._lock:
            if self._active_benchmark_id is not None:
                raise AsrBenchmarkError(
                    "another benchmark is already running "
                    f"({self._active_benchmark_id})"
                )
            payload = self.get_benchmark(benchmark_id)
            if payload["status"] in ACTIVE_STATUSES:
                raise AsrBenchmarkError(
                    f"benchmark is already {payload['status']}"
                )
            if payload["status"] in TERMINAL_STATUSES:
                # Re-run allowed from a terminal state: reset runs.
                payload["runs"] = []
                payload["status"] = STATUS_QUEUED
                payload["completed_at"] = None
                payload["error"] = None
                _atomic_write_json(self._benchmark_path(benchmark_id), payload)

            self._cancel_event.clear()
            self._active_benchmark_id = benchmark_id
        # Spawn the worker subprocess. The worker does the heavy lifting
        # (GPU lock, model load, transcribe, diagnostics, metrics) so the
        # FastAPI process stays responsive.
        worker_job = {
            "benchmark_id": benchmark_id,
            "benchmark_path": str(self._benchmark_path(benchmark_id)),
            "runs_dir": str(self._runs_dir(benchmark_id)),
            "source_type": payload["source_type"],
            "source_id": payload["source_id"],
            "preset_ids": payload["selected_presets"],
            "reference_text": payload.get("reference_text"),
            "hotwords": payload.get("hotwords"),
            "gpu_lock_dir": str(self.gpu_lock.data_dir),
        }
        bdir = self._benchmark_dir(benchmark_id)
        bdir.mkdir(parents=True, exist_ok=True)
        worker_job_path = bdir / "worker_job.json"
        with open(worker_job_path, "w", encoding="utf-8") as fh:
            json.dump(worker_job, fh, indent=2, ensure_ascii=False)
        log_path = bdir / "worker.log"
        try:
            log_fh = open(log_path, "wb", buffering=0)
        except OSError as exc:
            self._active_benchmark_id = None
            raise AsrBenchmarkError(f"could not open worker log: {exc}") from exc
        cmd = [sys.executable, "-m", "media_processing.asr_benchmark_worker", str(worker_job_path)]
        try:
            proc = subprocess.Popen(
                cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            try:
                log_fh.close()
            except OSError:
                pass
            self._active_benchmark_id = None
            raise AsrBenchmarkError(f"could not start worker: {exc}") from exc
        with self._lock:
            self._active_proc = proc
            self._active_log_fh = log_fh
        # Mark RUNNING.
        payload = self.get_benchmark(benchmark_id)
        payload["status"] = STATUS_RUNNING
        payload["started_at"] = _now_iso()
        _atomic_write_json(self._benchmark_path(benchmark_id), payload)
        # Start a reaper thread.
        threading.Thread(
            target=self._reap_worker,
            args=(benchmark_id, proc, log_fh),
            daemon=True,
            name=f"asr-benchmark-reaper-{benchmark_id}",
        ).start()
        return self.get_benchmark(benchmark_id)

    def _reap_worker(self, benchmark_id: str, proc: subprocess.Popen, log_fh: Any) -> None:
        try:
            exit_code = proc.wait()
        except Exception:  # pragma: no cover
            exit_code = -1
        try:
            log_fh.close()
        except OSError:
            pass
        with self._lock:
            self._active_proc = None
            self._active_benchmark_id = None
        # Finalise the benchmark status from the persisted runs.
        try:
            payload = self.get_benchmark(benchmark_id)
        except AsrBenchmarkNotFoundError:
            return
        runs = payload.get("runs") or []
        if self._cancel_event.is_set():
            payload["status"] = STATUS_CANCELED
            payload["error"] = "Benchmark was canceled by the user."
        else:
            ok = [r for r in runs if r.get("status") == STATUS_READY]
            failed = [r for r in runs if r.get("status") == STATUS_FAILED]
            if not ok and failed:
                payload["status"] = STATUS_FAILED
            elif failed and ok:
                payload["status"] = STATUS_PARTIALLY_FAILED
            elif ok:
                payload["status"] = STATUS_READY
            else:
                payload["status"] = STATUS_FAILED
            if exit_code != 0 and not runs:
                payload["error"] = (
                    f"benchmark worker exited with code {exit_code} before any run completed."
                )
        payload["completed_at"] = _now_iso()
        _atomic_write_json(self._benchmark_path(benchmark_id), payload)

    # ------------------------------------------------------------------ cancel
    def cancel(self, benchmark_id: str) -> dict[str, Any]:
        with self._lock:
            if self._active_benchmark_id != benchmark_id:
                # Not currently running — mark canceled on disk if still active.
                payload = self.get_benchmark(benchmark_id)
                if payload["status"] in ACTIVE_STATUSES:
                    payload["status"] = STATUS_CANCELED
                    payload["completed_at"] = _now_iso()
                    payload["error"] = "Benchmark was canceled by the user."
                    _atomic_write_json(self._benchmark_path(benchmark_id), payload)
                return payload
            self._cancel_event.set()
            proc = self._active_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except (OSError, ProcessLookupError):  # pragma: no cover
                pass
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except (OSError, ProcessLookupError):  # pragma: no cover
                    pass
        return self.get_benchmark(benchmark_id)

    # ------------------------------------------------------------------ delete
    def delete(self, benchmark_id: str) -> bool:
        with self._lock:
            if self._active_benchmark_id == benchmark_id:
                raise AsrBenchmarkError(
                    "cannot delete a running benchmark; cancel it first"
                )
        bdir = self._benchmark_dir(benchmark_id, raise_not_found=True)
        if not bdir.exists():
            raise AsrBenchmarkNotFoundError(f"benchmark not found: {benchmark_id}")
        tmp = bdir.with_name(bdir.name + ".deleting")
        try:
            os.replace(bdir, tmp)
        except OSError as exc:
            raise AsrBenchmarkError(f"could not delete benchmark: {exc}") from exc
        shutil.rmtree(tmp, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ helpers
    def is_running(self) -> bool:
        with self._lock:
            return self._active_benchmark_id is not None

    def _recover_on_startup(self) -> None:
        """Mark any benchmark left in an active state as FAILED."""
        for payload in self.list_benchmarks():
            if payload.get("status") in ACTIVE_STATUSES:
                payload["status"] = STATUS_FAILED
                payload["error"] = "Benchmark was interrupted by a server restart."
                payload["completed_at"] = _now_iso()
                try:
                    _atomic_write_json(self._benchmark_path(payload["id"]), payload)
                except OSError:  # pragma: no cover
                    pass


# ---------------------------------------------------------------------------
# Run finalisation (called by the worker after each preset transcribe)
# ---------------------------------------------------------------------------


def finalise_run(
    run_payload: dict[str, Any],
    audio_path: str,
    preset: AsrPreset,
    reference_text: Optional[str],
    compute_vad: bool,
    chunk_length: float = 30.0,
) -> dict[str, Any]:
    """Attach VAD diagnosis, metrics, hallucination and missing-speech
    flags to a run payload and return the updated dict.

    Called by the worker after a transcription completes. The VAD
    diagnosis is computed with the same Silero VAD faster-whisper uses
    so the timeline can distinguish VAD-removed audio from model output.
    """
    segments = run_payload.get("segments") or []
    audio_duration = run_payload.get("audio_duration_seconds")

    vad_diagnosis: Optional[VadDiagnosis] = None
    if compute_vad:
        vad_diagnosis = compute_vad_regions(
            audio_path, preset.vad_parameters, chunk_length=chunk_length
        )
        if audio_duration is None:
            audio_duration = vad_diagnosis.audio_duration_seconds
        run_payload["vad_diagnosis"] = vad_diagnosis.to_dict()
        run_payload["vad_diagnosis"]["computed"] = True
    else:
        run_payload["vad_diagnosis"] = {
            "computed": False,
            "audio_duration_seconds": audio_duration,
            "duration_after_vad_seconds": None,
            "removed_by_vad_seconds": None,
            "speech_regions": [],
        }

    hyp_text = hypothesis_text(segments)
    metrics = compute_metrics(reference_text, hyp_text)
    metrics_dict = metrics.to_dict()
    run_payload["metrics"] = metrics_dict

    run_payload["hallucination_flags"] = flag_hallucinations(
        segments, vad_diagnosis, metrics_dict
    )
    run_payload["missing_speech_flags"] = flag_missing_speech(
        segments, vad_diagnosis, audio_duration, metrics_dict
    )
    return run_payload


def recommend_winner(runs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Return the recommended winning run, or None if no run has metrics.

    Uses the transparent order from :func:`asr_metrics.rank_runs`. Never
    invents a winner when no run has ground-truth metrics.
    """
    ranked = rank_runs(runs)
    for run in ranked:
        metrics = run.get("metrics") or {}
        if metrics.get("available") and metrics.get("wer") is not None:
            return run
    return None
