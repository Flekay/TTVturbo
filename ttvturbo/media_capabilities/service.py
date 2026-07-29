"""Reusable subprocess job orchestration for media capabilities."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ttvturbo.lifecycle import terminate_subprocess
from ttvturbo.storage_utils import validate_uuid

from .storage import CapabilityNotFoundError, CapabilityStorage
from .utils import now_iso

logger = logging.getLogger("ttvturbo.media_capabilities")

WorkerRunner = Callable[[dict[str, Any], Path], None]


class SubprocessCapabilityService:
    """Base class for a single-worker media capability.

    Subclasses own validation, capability status and final artifact creation.
    The base owns process lifecycle, polling, cancellation, retry and orphan
    recovery. A test can inject ``worker_runner``; production always launches
    ``python -m <worker_module> <job-dir>``.
    """

    poll_seconds = 0.5

    def __init__(
        self,
        *,
        storage: CapabilityStorage,
        operation: str,
        worker_module: str,
        worker_python: Optional[str] = None,
        max_concurrent: int = 1,
        worker_runner: Optional[WorkerRunner] = None,
    ) -> None:
        self.storage = storage
        self.operation = operation
        self.worker_module = worker_module
        self.worker_python = worker_python or sys.executable
        self.max_concurrent = max(1, int(max_concurrent))
        self._worker_runner = worker_runner
        self._active: dict[str, subprocess.Popen[Any]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._shutdown = False
        self._recover_orphans()

    # ------------------------------------------------------------------ abstract hooks
    def runtime_status(self) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _finalize_job(self, job_id: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _prepare_retry(self, job: dict[str, Any]) -> dict[str, Any]:
        return job

    # ------------------------------------------------------------------ common API
    def get_job(self, job_id: str) -> dict[str, Any]:
        validate_uuid(job_id, "job", ValueError)
        self._maybe_finalize(job_id)
        return self.storage.load_job(job_id)

    def list_jobs(self, *, status_filter: Optional[str] = None) -> list[dict[str, Any]]:
        jobs = list(self.storage.iter_jobs())
        for job in jobs:
            if job.get("status") == "COMPLETED" and not job.get("output_artifact_id"):
                self._maybe_finalize(job["id"])
        jobs = list(self.storage.iter_jobs())
        if status_filter:
            jobs = [j for j in jobs if j.get("status") == status_filter]
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return jobs

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        validate_uuid(artifact_id, "artifact", ValueError)
        return self.storage.load_artifact(artifact_id)

    def list_artifacts(self) -> list[dict[str, Any]]:
        records = list(self.storage.iter_artifacts())
        records.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        return records

    def _assert_startable(self) -> None:
        if self._shutdown:
            raise RuntimeError(f"{self.operation} service is shutting down")
        status = self.runtime_status()
        if not status.get("available"):
            reasons = status.get("reasons") or [status.get("error") or "unavailable"]
            raise RuntimeError(f"{self.operation} is unavailable: {'; '.join(str(x) for x in reasons if x)}")
        with self._lock:
            active = sum(1 for p in self._active.values() if p.poll() is None)
            if active >= self.max_concurrent:
                raise RuntimeError(f"{self.operation} concurrency limit reached")

    def _start_prepared_job(self, job: dict[str, Any], worker_job: dict[str, Any]) -> dict[str, Any]:
        self._assert_startable()
        self.storage.save_job(job)
        self.storage.save_worker_job(job["id"], worker_job)
        self._launch(job["id"])
        if self._worker_runner is not None:
            self._maybe_finalize(job["id"])
        self._ensure_thread()
        return self.storage.load_job(job["id"])

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.storage.load_job(job_id)
        if job.get("status") not in {"QUEUED", "RUNNING", "RETRYING"}:
            raise RuntimeError(f"job is not active: {job.get('status')}")
        job["status"] = "CANCELED"
        job["completed_at"] = now_iso()
        job["current_stage"] = None
        self.storage.save_job(job)
        with self._lock:
            proc = self._active.pop(job_id, None)
        if proc is not None:
            terminate_subprocess(proc, label=f"{self.operation}:{job_id}")
        return self.storage.load_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        self._assert_startable()
        job = self.storage.load_job(job_id)
        if job.get("status") not in {"FAILED", "CANCELED"}:
            raise RuntimeError("retry requires FAILED or CANCELED job")
        job = self._prepare_retry(job)
        job.update({
            "status": "QUEUED",
            "progress": 0.0,
            "current_stage": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "output_artifact_id": None,
            "library_item_id": None,
            "attempt": int(job.get("attempt") or 1) + 1,
        })
        self.storage.save_job(job)
        self._launch(job_id)
        if self._worker_runner is not None:
            self._maybe_finalize(job_id)
        self._ensure_thread()
        return self.storage.load_job(job_id)

    # ------------------------------------------------------------------ process lifecycle
    def _launch(self, job_id: str) -> None:
        job_dir = self.storage.job_dir(job_id)
        if self._worker_runner is not None:
            try:
                self._worker_runner(self.storage.load_worker_job(job_id), job_dir)
            except Exception as exc:
                job = self.storage.load_job(job_id)
                job.update({
                    "status": "FAILED",
                    "completed_at": now_iso(),
                    "current_stage": None,
                    "error": {"code": "WORKER_FAILED", "message": str(exc), "retryable": True},
                })
                self.storage.save_job(job)
            return
        log_handle = self.storage.log_path(job_id).open("ab")
        proc = subprocess.Popen(
            [self.worker_python, "-m", self.worker_module, str(job_dir)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        # Popen keeps the descriptor open; parent copy can be closed.
        log_handle.close()
        with self._lock:
            self._active[job_id] = proc

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._poll_loop, name=f"{self.operation}-orchestrator", daemon=True)
            self._thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            jobs = list(self.storage.iter_jobs())
            any_active = False
            for job in jobs:
                status = job.get("status")
                if status in {"QUEUED", "RUNNING", "RETRYING"}:
                    any_active = True
                    self._handle_worker_exit(job["id"])
                elif status == "COMPLETED" and not job.get("output_artifact_id"):
                    self._maybe_finalize(job["id"])
            if not any_active:
                # Keep the thread alive briefly; a new job can reuse it.
                continue

    def _handle_worker_exit(self, job_id: str) -> None:
        with self._lock:
            proc = self._active.get(job_id)
        if proc is not None and proc.poll() is None:
            return
        if proc is not None:
            with self._lock:
                self._active.pop(job_id, None)
        try:
            job = self.storage.load_job(job_id)
        except Exception:
            return
        if job.get("status") == "COMPLETED":
            self._maybe_finalize(job_id)
            return
        if job.get("status") in {"QUEUED", "RUNNING", "RETRYING"}:
            result = self.storage.load_result(job_id) or {}
            job.update({
                "status": "FAILED",
                "completed_at": now_iso(),
                "current_stage": None,
                "error": {
                    "code": "WORKER_EXITED",
                    "message": result.get("error") or "worker exited without completing the job",
                    "retryable": True,
                },
            })
            self.storage.save_job(job)

    def _maybe_finalize(self, job_id: str) -> None:
        try:
            self._finalize_job(job_id)
        except Exception as exc:
            try:
                job = self.storage.load_job(job_id)
                if job.get("status") == "COMPLETED" and not job.get("output_artifact_id"):
                    job.update({
                        "status": "FAILED",
                        "completed_at": now_iso(),
                        "current_stage": None,
                        "error": {"code": "FINALIZE_FAILED", "message": str(exc), "retryable": True},
                    })
                    self.storage.save_job(job)
            except Exception:
                logger.exception("could not mark finalization failure for %s", job_id)

    def _recover_orphans(self) -> None:
        for job in list(self.storage.iter_jobs()):
            if job.get("status") in {"RUNNING", "RETRYING"}:
                job.update({
                    "status": "FAILED",
                    "completed_at": now_iso(),
                    "current_stage": None,
                    "error": {
                        "code": "WORKER_ORPHANED",
                        "message": "worker was active during a previous process and must be retried",
                        "retryable": True,
                    },
                })
                self.storage.save_job(job)
            elif job.get("status") == "COMPLETED" and not job.get("output_artifact_id"):
                self._maybe_finalize(job["id"])

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._stop.set()
        with self._lock:
            procs = list(self._active.items())
            self._active.clear()
        for job_id, proc in procs:
            terminate_subprocess(proc, label=f"{self.operation}:{job_id}")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
