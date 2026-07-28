"""Minimal service lifecycle contract for TTVturbo.

Every long-lived service that owns subprocesses, threads, or open handles
implements a ``shutdown()`` method with these guarantees:

1. **No new jobs**: after ``shutdown()`` is called, the service refuses
   new work (or is simply not called again by the app).
2. **Controlled termination**: active subprocesses receive a graceful
   ``terminate()`` first, then a hard ``kill()`` after a grace period.
3. **Handle cleanup**: log file handles are closed.
4. **Idempotent**: calling ``shutdown()`` twice is safe and does nothing
   on the second call.
5. **Partial-failure tolerant**: a failure in one service's shutdown does
   not prevent the remaining services from shutting down.

The FastAPI lifespan calls ``shutdown()`` on every service that has the
method, in reverse order of initialisation.

Services without subprocesses or threads (storage, library, voice-profile
store, …) do not need ``shutdown()`` and are simply not called.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger("ttvturbo.lifecycle")

# Grace period between a graceful terminate() and a hard kill().
DEFAULT_GRACE_SECONDS = 5.0


def terminate_subprocess(
    proc: subprocess.Popen,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    label: str = "worker",
) -> None:
    """Gracefully terminate *proc*, then hard-kill if it does not exit.

    Idempotent: safe to call on an already-exited process.  Logs but never
    raises — a shutdown failure in one subprocess must not block the rest.
    """
    if proc.poll() is not None:
        return  # already exited
    try:
        proc.terminate()
    except (OSError, ProcessLookupError):
        return
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except (OSError, ProcessLookupError):
        return
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:  # pragma: no cover - very unlikely
        logger.warning("Hard kill of %s did not exit within %.1fs", label, grace_seconds)


def shutdown_service(service: Any) -> None:
    """Call ``shutdown()`` on *service* if it has the method.

    Logs and swallows exceptions so one failed shutdown does not block the
    remaining services.  Idempotent.
    """
    method = getattr(service, "shutdown", None)
    if method is None:
        return
    try:
        method()
    except Exception as exc:  # noqa: BLE001
        logger.warning("shutdown() failed for %s: %s", type(service).__name__, exc)
