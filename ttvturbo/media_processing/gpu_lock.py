"""Project-wide cross-process GPU lock.

TTVturbo has a single 12 GB GPU (RTX 5070). Two GPU-bound workloads must
not load their models at the same time:

* Qwen3-TTS voice-clone (``voice_clone.runtime`` subprocess);
* faster-whisper transcription (``media_processing.transcription_worker``
  subprocess).

This module provides a file-based, cross-process lock with:

* owner type (``"voice_clone"`` / ``"transcription"``) and job id;
* owner PID for stale-lock detection;
* atomic acquisition via a unique tmp file + ``os.replace``;
* release in a ``finally`` block by the owning process;
* stale-lock reaping on server restart (the lock file is inspected, and
  if the recorded PID is no longer alive the lock is reclaimed);
* no in-memory boolean — the lock state lives entirely on disk so two
  separate Python processes see the same owner.

The lock is intentionally simple: a single global GPU, a single
exclusive owner. No scheduler, no queue, no cluster.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ttvturbo.media_processing.gpu_lock")

LOCK_FILENAME = "gpu.lock"

# Valid owner types. The frontend shows these labels so the user can see
# what is currently holding the GPU.
OWNER_VOICE_CLONE = "voice_clone"
OWNER_TRANSCRIPTION = "transcription"
OWNER_VIDEO_GENERATION = "video_generation"
VALID_OWNER_TYPES = frozenset({OWNER_VOICE_CLONE, OWNER_TRANSCRIPTION, OWNER_VIDEO_GENERATION})


class GpuLockError(Exception):
    """Base class for GPU-lock errors."""


class GpuLockBusyError(GpuLockError):
    """The GPU is currently held by another owner.

    Carries the current owner info so the caller can surface a concrete
    "GPU belegt durch Voice Clone / Transcription" message.
    """

    def __init__(self, owner: dict[str, Any]) -> None:
        self.owner = owner
        owner_type = owner.get("owner_type", "unknown")
        job_id = owner.get("job_id")
        msg = f"GPU is busy (owner_type={owner_type}"
        if job_id:
            msg += f", job_id={job_id}"
        msg += ")"
        super().__init__(msg)


class GpuLockOwner:
    """Context manager that acquires the GPU lock for a single job.

    Usage::

        with GpuLockOwner(lock, owner_type="transcription", job_id=jid):
            ...load model and run...

    The lock is released in ``__exit__`` regardless of exceptions. If the
    GPU is already busy, :class:`GpuLockBusyError` is raised on enter.
    """

    def __init__(
        self,
        lock: "GpuLock",
        owner_type: str,
        job_id: str,
        timeout_seconds: float = 0.0,
        poll_interval: float = 1.0,
    ) -> None:
        if owner_type not in VALID_OWNER_TYPES:
            raise GpuLockError(f"invalid owner_type {owner_type!r}")
        self._lock = lock
        self._owner_type = owner_type
        self._job_id = job_id
        self._timeout = float(timeout_seconds)
        self._poll = float(poll_interval)
        self._acquired = False

    def __enter__(self) -> "GpuLockOwner":
        deadline = None
        if self._timeout > 0:
            deadline = time.monotonic() + self._timeout
        last_busy: Optional[dict[str, Any]] = None
        while True:
            try:
                self._lock.acquire(self._owner_type, self._job_id)
                self._acquired = True
                return self
            except GpuLockBusyError as exc:
                last_busy = exc.owner
                if deadline is not None and time.monotonic() >= deadline:
                    raise
                time.sleep(self._poll)
        # unreachable

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._acquired:
            self._lock.release(self._owner_type, self._job_id)
            self._acquired = False


class GpuLock:
    """File-based cross-process GPU lock.

    The lock file lives at ``{data_dir}/gpu.lock`` and contains a small
    JSON document::

        {
          "owner_type": "transcription",
          "job_id": "uuid",
          "pid": 12345,
          "acquired_at": "UTC ISO-8601",
          "host": "..."
        }

    Acquisition is atomic: a unique tmp file is written and ``os.replace``d
    onto the lock path. A successful replace means we own the lock; we then
    re-read the file to confirm our content is what landed (defending
    against a rare replace race on shared filesystems). Release deletes the
    lock file.

    Stale-lock detection: if the recorded PID is no longer alive (or the
    lock file is older than ``stale_seconds``), the lock is reclaimed.
    """

    def __init__(
        self,
        data_dir: Path,
        stale_seconds: float = 3600.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.data_dir / LOCK_FILENAME
        self.stale_seconds = float(stale_seconds)

    # ------------------------------------------------------------------ public
    def acquire(self, owner_type: str, job_id: str) -> None:
        """Acquire the lock. Raises :class:`GpuLockBusyError` if held."""
        if owner_type not in VALID_OWNER_TYPES:
            raise GpuLockError(f"invalid owner_type {owner_type!r}")
        if not isinstance(job_id, str) or not job_id:
            raise GpuLockError("job_id must be a non-empty string")
        payload = {
            "owner_type": owner_type,
            "job_id": job_id,
            "pid": os.getpid(),
            "acquired_at": _now_iso(),
            "host": os.environ.get("COMPUTERNAME") or os.uname().nodename if hasattr(os, "uname") else "",
        }
        # Reap a stale lock first.
        self._reap_stale()
        # Try a direct acquire.
        if self._try_acquire(payload):
            return
        # If the current owner is us (same owner_type + job_id + pid), allow
        # re-entrancy — the worker may call acquire twice in a retry loop.
        current = self._read_current()
        if (
            current
            and current.get("owner_type") == owner_type
            and current.get("job_id") == job_id
            and current.get("pid") == os.getpid()
        ):
            return
        raise GpuLockBusyError(current or {"owner_type": "unknown"})

    def release(self, owner_type: str, job_id: str) -> None:
        """Release the lock if we still own it. Idempotent."""
        current = self._read_current()
        if current is None:
            return
        if (
            current.get("owner_type") == owner_type
            and current.get("job_id") == job_id
            and current.get("pid") == os.getpid()
        ):
            self._delete()

    def current_owner(self) -> Optional[dict[str, Any]]:
        """Return the current owner dict, or None if the GPU is free.

        Reaps a stale lock before reporting.
        """
        self._reap_stale()
        return self._read_current()

    def is_busy(self) -> bool:
        return self.current_owner() is not None

    # ------------------------------------------------------------------ internal
    def _try_acquire(self, payload: dict[str, Any]) -> bool:
        """Atomically create the lock file. Returns True on success."""
        tmp = self.lock_path.with_name(
            f".{self.lock_path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            # O_CREAT | O_EXCL semantics via os.replace would not be atomic
            # on Windows if the target exists. We accept that a stale lock
            # was already reaped; if the target exists we lost the race.
            if self.lock_path.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return False
            try:
                os.replace(tmp, self.lock_path)
            except OSError:
                # On some platforms os.replace onto a non-existing target
                # is fine; if it failed, treat as lost race.
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return False
        except OSError as exc:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise GpuLockError(f"could not write gpu lock: {exc}") from exc
        # Confirm our content landed (defends against a replace race).
        current = self._read_current()
        return (
            current is not None
            and current.get("owner_type") == payload["owner_type"]
            and current.get("job_id") == payload["job_id"]
            and current.get("pid") == payload["pid"]
        )

    def _read_current(self) -> Optional[dict[str, Any]]:
        if not self.lock_path.is_file():
            return None
        try:
            with open(self.lock_path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _delete(self) -> None:
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("could not remove gpu lock %s: %s", self.lock_path, exc)

    def _reap_stale(self) -> None:
        current = self._read_current()
        if current is None:
            return
        pid = current.get("pid")
        if isinstance(pid, int) and pid > 0:
            if not _pid_alive(pid):
                logger.warning(
                    "Reaping stale GPU lock: owner_type=%s job_id=%s pid=%s no longer alive.",
                    current.get("owner_type"),
                    current.get("job_id"),
                    pid,
                )
                self._delete()
                return
        acquired_at = current.get("acquired_at")
        if isinstance(acquired_at, str):
            try:
                ts = _dt.datetime.fromisoformat(acquired_at)
                age = (_dt.datetime.now(tz=_dt.timezone.utc) - ts).total_seconds()
                if age > self.stale_seconds:
                    logger.warning(
                        "Reaping stale GPU lock: age=%.0fs > %.0fs.",
                        age,
                        self.stale_seconds,
                    )
                    self._delete()
            except (ValueError, TypeError):
                pass


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` is currently alive.

    Cross-platform: uses ``os.kill(pid, 0)`` on POSIX and
    ``ctypes.OpenProcess`` on Windows. On any error, returns True so we
    never reap a lock that might still be valid (fail-safe).
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                # ERROR_ACCESS_DENIED means the process exists but we cannot
                # query it — treat as alive.
                err = ctypes.windll.kernel32.GetLastError()
                return err == 5  # ERROR_ACCESS_DENIED
            try:
                exit_code = wintypes.DWORD()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if not ok:
                    return True
                return exit_code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:  # pragma: no cover - defensive
            return True
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()
