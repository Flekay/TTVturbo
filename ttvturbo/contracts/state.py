"""State-machine and progress helpers for :class:`OperationJob`.

These helpers are pure functions on status / progress strings.  They do
not touch persistence and do not import any domain module, so they can
be unit-tested in isolation and reused by every tool that adopts the
contract.
"""

from __future__ import annotations

from .models import (
    ACTIVE_JOB_STATUSES,
    ALLOWED_JOB_TRANSITIONS,
    CANCELLABLE_JOB_STATUSES,
    JobStatus,
    TERMINAL_JOB_STATUSES,
)


class InvalidJobTransitionError(ValueError):
    """Raised when a requested status transition is not allowed."""


class InvalidJobProgressError(ValueError):
    """Raised when a progress value is outside the 0..100 range."""


def is_terminal(status: str) -> bool:
    """True if *status* is a terminal job status (no outgoing edges)."""
    return status in TERMINAL_JOB_STATUSES


def is_active(status: str) -> bool:
    """True if *status* is an active (non-terminal, in-flight) status."""
    return status in ACTIVE_JOB_STATUSES


def is_cancellable(status: str) -> bool:
    """True if a job in *status* may be requested to cancel."""
    return status in CANCELLABLE_JOB_STATUSES


def assert_transition(from_status: str, to_status: str) -> None:
    """Raise :class:`InvalidJobTransitionError` if the move is not allowed.

    Rules:

    * ``from_status`` and ``to_status`` must be known
      :class:`JobStatus` values;
    * terminal states have no outgoing transitions;
    * the move must be listed in :data:`ALLOWED_JOB_TRANSITIONS`.

    The optional ``CANCELING`` / ``RETRYING`` states are valid targets
    but a tool may always collapse them into ``RUNNING`` — i.e. the
    minimal path ``QUEUED -> RUNNING -> COMPLETED`` is always allowed.
    """
    valid = {s.value for s in JobStatus}
    if from_status not in valid:
        raise InvalidJobTransitionError(
            f"unknown from_status {from_status!r}; expected one of {sorted(valid)}"
        )
    if to_status not in valid:
        raise InvalidJobTransitionError(
            f"unknown to_status {to_status!r}; expected one of {sorted(valid)}"
        )
    allowed = ALLOWED_JOB_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise InvalidJobTransitionError(
            f"transition {from_status} -> {to_status} is not allowed; "
            f"allowed targets: {sorted(allowed) or '<terminal>'}"
        )


def transition(from_status: str, to_status: str) -> str:
    """Validate and return *to_status*.

    Convenience wrapper around :func:`assert_transition` for inline use::

        job["status"] = transition(job["status"], JobStatus.RUNNING.value)
    """
    assert_transition(from_status, to_status)
    return to_status


def validate_progress(value: float | None) -> float | None:
    """Clamp/validate a progress percentage in the inclusive 0..100 range.

    ``None`` is allowed (means "not yet reported").  Values outside the
    range raise :class:`InvalidJobProgressError` — we do not silently
    clamp, because a value like ``120`` or ``-5`` almost always indicates
    a bug in the producing worker.
    """
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise InvalidJobProgressError(
            f"progress must be a number or None, got {type(value).__name__}"
        )
    if value < 0 or value > 100:
        raise InvalidJobProgressError(
            f"progress must be in [0, 100], got {value}"
        )
    return float(value)
