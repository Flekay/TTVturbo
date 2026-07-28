"""Shared backend capability contracts.

Small, validated models that all media tools can reuse so they speak the
same language about media references, artifact references, operation jobs,
unified errors and capability status.

Scope rules (see project spec):

* This is **not** a generic plugin system and **not** a universal
  ``/operations/run-anything`` API.  Each domain keeps its own endpoints
  and service code; nothing here imports from any domain package.
* Contracts cover only the recurring **technical** fields
  (ids, status, progress, timestamps, error shape).  Domain-specific
  payload stays in the domain schemas.
* Existing persisted formats are not touched.  The models here are
  additive: domains may adopt them incrementally without breaking
  already-written JSON.
* No FastAPI, no React, no I/O.  Pure pydantic + enum + helpers.

The dependency direction is::

    app / api -> services -> domain schemas
                                   ^
                                   |
                            ttvturbo.contracts

No domain package imports this module yet; adoption is opt-in.  The
module itself imports only the standard library and pydantic, so it
cannot create a circular import with any domain.
"""

from __future__ import annotations

from .models import (
    ArtifactReference,
    CapabilityStatus,
    JobStatus,
    MediaReference,
    OperationError,
    OperationJob,
    TERMINAL_JOB_STATUSES,
    ACTIVE_JOB_STATUSES,
    CANCELLABLE_JOB_STATUSES,
    ALLOWED_JOB_TRANSITIONS,
    DEFAULT_JOB_OPERATION,
)
from .state import (
    InvalidJobTransitionError,
    InvalidJobProgressError,
    assert_transition,
    is_terminal,
    is_active,
    is_cancellable,
    validate_progress,
    transition,
)

__all__ = [
    "ArtifactReference",
    "CapabilityStatus",
    "JobStatus",
    "MediaReference",
    "OperationError",
    "OperationJob",
    "TERMINAL_JOB_STATUSES",
    "ACTIVE_JOB_STATUSES",
    "CANCELLABLE_JOB_STATUSES",
    "ALLOWED_JOB_TRANSITIONS",
    "DEFAULT_JOB_OPERATION",
    "InvalidJobTransitionError",
    "InvalidJobProgressError",
    "assert_transition",
    "is_terminal",
    "is_active",
    "is_cancellable",
    "validate_progress",
    "transition",
]
