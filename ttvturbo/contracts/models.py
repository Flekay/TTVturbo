"""Pydantic models for the shared backend capability contracts.

Every model is **additive** and uses ``Optional`` + ``default_factory``
so it can be layered on top of existing domain JSON without breaking
already-persisted records.  Domain schemas stay the source of truth for
domain-specific payload; the contracts here only normalise the
recurring technical fields (ids, status, progress, timestamps, error
shape, capability flags).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    """Lifecycle status of an :class:`OperationJob`.

    The five core states (``QUEUED``, ``RUNNING``, ``COMPLETED``,
    ``FAILED``, ``CANCELED``) are mandatory for every tool that adopts
    the contract.  ``CANCELING`` and ``RETRYING`` are **optional** and
    only used by tools that already had them — they are valid transition
    targets but a tool may collapse them into ``RUNNING`` instead.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    # Optional — only emit if the tool already uses these states.
    CANCELING = "CANCELING"
    RETRYING = "RETRYING"


# Core terminal / active / cancellable sets — defined by value so they
# work regardless of whether a tool uses the optional states.

TERMINAL_JOB_STATUSES: frozenset[str] = frozenset({
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELED.value,
})

ACTIVE_JOB_STATUSES: frozenset[str] = frozenset({
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.CANCELING.value,
    JobStatus.RETRYING.value,
})

CANCELLABLE_JOB_STATUSES: frozenset[str] = frozenset({
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
})


# Allowed forward transitions.  Terminal states have no outgoing edges.
# A tool may skip the optional states (CANCELING / RETRYING) — the
# transition QUEUED -> RUNNING -> COMPLETED is always valid.
ALLOWED_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    JobStatus.QUEUED.value: frozenset({
        JobStatus.RUNNING.value,
        JobStatus.CANCELED.value,
        JobStatus.FAILED.value,
    }),
    JobStatus.RUNNING.value: frozenset({
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
        JobStatus.CANCELING.value,
        JobStatus.RETRYING.value,
    }),
    JobStatus.CANCELING.value: frozenset({
        JobStatus.CANCELED.value,
        JobStatus.FAILED.value,
    }),
    JobStatus.RETRYING.value: frozenset({
        JobStatus.RUNNING.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
    }),
    # Terminal states: no outgoing transitions.
    JobStatus.COMPLETED.value: frozenset(),
    JobStatus.FAILED.value: frozenset(),
    JobStatus.CANCELED.value: frozenset(),
}


# Default operation label when a tool does not supply a domain-specific
# one (e.g. internal helper jobs).  Real tools should always pass their
# own operation string (e.g. ``"facecam_enhancement"``).
DEFAULT_JOB_OPERATION = "operation"


# ---------------------------------------------------------------------------
# Media reference
# ---------------------------------------------------------------------------

class MediaReference(BaseModel):
    """A pointer into a media item, optionally bounded to a time range.

    Used as an input reference for an :class:`OperationJob` and as the
    parent of an :class:`ArtifactReference`.  All fields except
    ``media_item_id`` are optional so the model can describe a whole
    item, a single asset within it, or a sub-range of an asset.
    """

    model_config = ConfigDict(extra="ignore")

    media_item_id: str
    asset_id: Optional[str] = None
    start_seconds: Optional[float] = Field(default=None, ge=0.0)
    end_seconds: Optional[float] = Field(default=None, ge=0.0)
    source_revision: Optional[str] = None

    @field_validator("end_seconds")
    @classmethod
    def _end_after_start(cls, value: Optional[float], info) -> Optional[float]:
        start = info.data.get("start_seconds")
        if value is not None and start is not None and value < start:
            raise ValueError(
                f"end_seconds ({value}) must be >= start_seconds ({start})"
            )
        return value


# ---------------------------------------------------------------------------
# Artifact reference
# ---------------------------------------------------------------------------

class ArtifactReference(BaseModel):
    """A reference to an artifact produced by an operation.

    The ``artifact_type`` is a free-form short string owned by the
    producing domain (e.g. ``"audio"``, ``"transcript"``).  The
    ``revision`` is a short opaque string the domain uses to detect
    stale references; the contract does not interpret it.
    """

    model_config = ConfigDict(extra="ignore")

    artifact_id: str
    artifact_type: str
    media_item_id: str
    created_at: str
    revision: str = "1"


# ---------------------------------------------------------------------------
# Unified error
# ---------------------------------------------------------------------------

class OperationError(BaseModel):
    """Unified error shape for every operation job.

    Domains keep their existing exception classes; when an error is
    surfaced on a job, it is normalised into this shape so the frontend
    and any cross-tool orchestrator can rely on a single contract.

    ``code`` is a SCREAMING_SNAKE machine-readable string.
    ``retryable`` is a hint, not a promise.
    ``details`` is an opaque bag for domain-specific context.
    """

    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _code_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("error code must not be empty")
        return value

    @field_validator("message")
    @classmethod
    def _message_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("error message must not be empty")
        return value


# ---------------------------------------------------------------------------
# Operation job
# ---------------------------------------------------------------------------

class OperationJob(BaseModel):
    """A unified, validated job record.

    Domains that already persist their own job JSON (e.g.
    ``media_jobs/{job_id}/job.json``) do **not** need to migrate.  They
    can either:

    * adopt this model as their persisted shape (additive fields only),
      or
    * project their internal record into this model at the API boundary.

    Both paths are backward compatible because every field here is
    optional except ``id`` and ``operation``.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    operation: str = DEFAULT_JOB_OPERATION
    status: str = JobStatus.QUEUED.value

    # 0..100 inclusive.  ``None`` means "not yet reported".
    progress: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    current_stage: Optional[str] = None

    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    input_references: list[MediaReference] = Field(default_factory=list)
    output_artifacts: list[ArtifactReference] = Field(default_factory=list)

    error: Optional[OperationError] = None

    @field_validator("status")
    @classmethod
    def _status_known(cls, value: str) -> str:
        valid = {s.value for s in JobStatus}
        if value not in valid:
            raise ValueError(
                f"unknown job status {value!r}; expected one of {sorted(valid)}"
            )
        return value

    @field_validator("operation")
    @classmethod
    def _operation_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("operation must not be empty")
        return value


# ---------------------------------------------------------------------------
# Capability status
# ---------------------------------------------------------------------------

class CapabilityStatus(BaseModel):
    """Availability status of a single backend capability.

    A capability is anything a tool exposes that may be unavailable at
    runtime: a model that needs a GPU, an external CLI, a remote API
    key, etc.  The shape is intentionally tiny so it can be aggregated
    into a single ``GET /api/capabilities`` response later without
    coupling the tools together.

    * ``available`` — the capability can be invoked right now.
    * ``configured`` — the required settings / secrets / paths are set.
      A capability may be configured but not available (e.g. model
      weights missing) or available but not configured (rare, e.g.
      defaults that work without config).
    * ``busy`` — the capability is currently running a job and cannot
      accept another one (single-slot tools).
    * ``reason`` — short human-readable explanation when
      ``available`` is false; ``null`` when available.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    available: bool
    configured: bool = False
    busy: bool = False
    reason: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("capability id must not be empty")
        return value

    @field_validator("reason")
    @classmethod
    def _reason_only_when_unavailable(
        cls, value: Optional[str], info
    ) -> Optional[str]:
        available = info.data.get("available")
        if available is True and value:
            raise ValueError(
                "reason must be null when available is true"
            )
        return value
