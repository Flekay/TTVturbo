"""Editor-command service: parses natural-language editor commands via a
local LLM.

The service reuses the same local text model that Conversation Mining uses
(``conversation_mining_model_id`` / ``conversation_mining_thinking_enabled``)
and the same shared cross-process GPU lock.  It spawns a one-shot worker
subprocess (``conversation_mining_worker --editor-command``) that loads the
model, runs a single inference, and writes the parsed intent JSON back to a
file.  The FastAPI process never imports transformers / torch.

The returned intent is a plain dict (e.g. ``{"action": "move", "axis": "x",
"direction": "right", "amount": 10, "unit": "percent"}``) that the frontend
applies through the existing editor operation functions.  The service does
NOT commit any operations itself — it is a pure intent parser.

When the model is not configured or the GPU dependencies are missing the
service raises :class:`EditorCommandUnavailableError` so the API can surface
a clean 503.  There is no regex fallback.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ttvturbo.settings import Settings
from ttvturbo.storage_utils import atomic_write_json

from .gpu_lock import GpuLock

logger = logging.getLogger("ttvturbo.media_processing.editor_command")

# Subdir (under paths.editor_commands) where one-shot jobs live.
EDITOR_COMMAND_SUBDIR = "editor_commands"
JOB_JSON = "job.json"
RESULT_JSON = "result.json"
WORKER_LOG = "worker.log"

# Default synchronous wait for the worker.  Model load + one short inference
# on a 12 GB GPU is well under this; the bound protects against a hung worker.
DEFAULT_TIMEOUT_SECONDS = 180.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EditorCommandError(Exception):
    """Base class for editor-command errors."""


class EditorCommandUnavailableError(EditorCommandError):
    """The local LLM is not configured / dependencies are missing."""


class EditorCommandTimeoutError(EditorCommandError):
    """The worker subprocess did not finish within the timeout."""


class EditorCommandValidationError(EditorCommandError):
    """The request was malformed (e.g. empty command)."""


class EditorCommandWorkerError(EditorCommandError):
    """The worker ran but reported a failure (model load, generation, parse)."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EditorCommandService:
    """Parses natural-language editor commands via the local text LLM.

    The service is synchronous: ``parse`` blocks until the worker finishes
    (or the timeout elapses).  It is designed for short, interactive
    editor commands, not batch processing.
    """

    def __init__(
        self,
        gpu_lock: GpuLock,
        settings: Settings,
        worker_python: Optional[str] = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.gpu_lock = gpu_lock
        self.settings = settings
        self._worker_python = worker_python or sys.executable
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def runtime_status(self) -> dict:
        """Return the editor-LLM availability status.

        Mirrors the conversation-mining preconditions because the editor
        reuses the same model and worker module.
        """
        model_id = (self.settings.conversation_mining_model_id or "").strip()
        model_configured = bool(model_id)
        deps_ok, dep_reason = self._check_dependencies()
        cuda_available = self._check_cuda_available()
        device = self.settings.conversation_mining_device or "cuda"
        cuda_relevant = device.lower().startswith("cuda")
        worker_available = self._check_worker_module()
        reasons: list[str] = []
        if not model_configured:
            reasons.append("no model configured")
        if not deps_ok:
            reasons.append(dep_reason or "dependencies missing")
        if cuda_relevant and not cuda_available:
            reasons.append("CUDA not available")
        if not worker_available:
            reasons.append("worker module not importable")
        available = (
            model_configured
            and deps_ok
            and worker_available
            and (not cuda_relevant or cuda_available)
        )
        return {
            "available": available,
            "model_configured": model_configured,
            "dependencies_available": deps_ok,
            "cuda_available": cuda_available,
            "worker_available": worker_available,
            "model": model_id,
            "device": device,
            "dtype": self.settings.conversation_mining_dtype,
            "thinking_enabled": self.settings.conversation_mining_thinking_enabled,
            "busy": self.gpu_lock.is_busy(),
            "busy_owner_type": (self.gpu_lock.current_owner() or {}).get("owner_type"),
            "reasons": reasons,
            "error": reasons[0] if reasons else None,
        }

    def preflight(self) -> tuple[bool, list[str]]:
        """Pre-call validation. Returns ``(ok, reasons)``."""
        status = self.runtime_status()
        reasons: list[str] = []
        if not status.get("model_configured"):
            reasons.append("editor command model is not configured")
        if not status.get("dependencies_available"):
            reasons.append("editor command worker dependencies missing (transformers/torch)")
        if not status.get("worker_available"):
            reasons.append("editor command worker module not importable")
        if (
            status.get("cuda_available") is False
            and (status.get("device") or "").lower().startswith("cuda")
        ):
            reasons.append("CUDA not available for editor command device")
        return (len(reasons) == 0, reasons)

    def parse(self, command: str, context: Optional[dict] = None) -> dict:
        """Parse *command* into a structured intent dict.

        Raises :class:`EditorCommandValidationError` for empty input,
        :class:`EditorCommandUnavailableError` when the LLM is not ready,
        :class:`EditorCommandTimeoutError` on timeout, or
        :class:`EditorCommandWorkerError` on a worker-reported failure.
        """
        normalized = (command or "").strip()
        if not normalized:
            raise EditorCommandValidationError("command is empty")
        ctx = context or {}

        ok, reasons = self.preflight()
        if not ok:
            raise EditorCommandUnavailableError("; ".join(reasons))

        paths = self.settings.paths()
        base = paths.editor_commands / EDITOR_COMMAND_SUBDIR
        base.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        run_dir = base / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        job_path = run_dir / JOB_JSON
        result_path = run_dir / RESULT_JSON
        log_path = run_dir / WORKER_LOG

        job = {
            "run_id": run_id,
            "command": normalized,
            "context": ctx,
            "model_id": self.settings.conversation_mining_model_id,
            "device": self.settings.conversation_mining_device,
            "dtype": self.settings.conversation_mining_dtype,
            "max_new_tokens": 512,
            "max_input_tokens": 4096,
            "thinking_enabled": self.settings.conversation_mining_thinking_enabled,
            "gpu_lock_data_dir": str(self.gpu_lock.data_dir),
            "gpu_lock_stale_seconds": self.settings.gpu_lock_stale_seconds,
            "model_cache_dir": self.settings.asr_model_cache_dir,
            "output_path": str(result_path),
        }
        atomic_write_json(job_path, job, EditorCommandError, kind="editor-command-job")

        proc = self._spawn_worker(job_path, log_path)
        try:
            self._wait(proc, run_id)
        except EditorCommandTimeoutError:
            self._terminate(proc, run_id)
            raise
        return self._read_result(result_path, run_id)

    # ------------------------------------------------------------------ helpers
    def _spawn_worker(self, job_path: Path, log_path: Path) -> subprocess.Popen:
        cmd = [
            self._worker_python,
            "-m",
            "ttvturbo.media_processing.conversation_mining_worker",
            "--editor-command",
            str(job_path),
        ]
        log_fh = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            log_fh.close()
            raise EditorCommandError(f"could not start editor-command worker: {exc}") from exc
        try:
            log_fh.close()
        except Exception:
            pass
        return proc

    def _wait(self, proc: subprocess.Popen, run_id: str) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            rc = proc.poll()
            if rc is not None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EditorCommandTimeoutError(
                    f"editor-command worker {run_id} timed out after {self._timeout_seconds}s"
                )
            # Poll at a short interval so cancellation stays responsive.
            time.sleep(min(0.5, remaining))

    def _terminate(self, proc: subprocess.Popen, run_id: str) -> None:
        try:
            from ttvturbo.lifecycle import terminate_subprocess
            terminate_subprocess(proc, label=f"editor-command-worker-{run_id}")
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _read_result(self, result_path: Path, run_id: str) -> dict:
        if not result_path.is_file():
            raise EditorCommandWorkerError(
                f"editor-command worker {run_id} produced no result"
            )
        import json
        try:
            with open(result_path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
        except Exception as exc:
            raise EditorCommandWorkerError(
                f"editor-command worker {run_id} wrote unreadable result: {exc}"
            ) from exc
        if not isinstance(payload, dict) or "ok" not in payload:
            raise EditorCommandWorkerError(
                f"editor-command worker {run_id} wrote malformed result"
            )
        if not payload.get("ok"):
            raise EditorCommandWorkerError(
                str(payload.get("error") or "editor-command worker failed")
            )
        intent = payload.get("intent")
        if not isinstance(intent, dict) or "action" not in intent:
            raise EditorCommandWorkerError(
                f"editor-command worker {run_id} returned no intent"
            )
        return intent

    def _check_dependencies(self) -> tuple[bool, Optional[str]]:
        try:
            import transformers  # noqa: F401
        except ImportError:
            return False, "transformers is not installed (see requirements-gpu.txt)"
        try:
            import torch  # noqa: F401
        except ImportError:
            return False, "torch is not installed (see requirements-gpu.txt)"
        return True, None

    def _check_cuda_available(self) -> bool:
        try:
            import torch  # noqa: F401
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _check_worker_module(self) -> bool:
        try:
            import importlib
            importlib.import_module("ttvturbo.media_processing.conversation_mining_worker")
            return True
        except Exception:
            return False
