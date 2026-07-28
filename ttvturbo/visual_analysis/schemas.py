"""Schemas for the Visual Analysis backend capability.

Pure pydantic models, enums and typed errors. No FastAPI, no I/O, no
domain imports.  Coordinates are **normalised** between 0 and 1 on both
axes so artifacts are resolution-independent.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Region types
# ---------------------------------------------------------------------------

class RegionType(str, Enum):
    """The five region types a visual analysis can detect."""

    GAMEPLAY = "GAMEPLAY"
    FACECAM = "FACECAM"
    CHAT = "CHAT"
    OVERLAY = "OVERLAY"
    UNKNOWN = "UNKNOWN"


REGION_TYPES: frozenset[str] = frozenset(r.value for r in RegionType)


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

class VisualAnalysisJobStatus:
    """Lifecycle status of a visual-analysis job."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


ACTIVE_JOB_STATUSES = frozenset({
    VisualAnalysisJobStatus.QUEUED,
    VisualAnalysisJobStatus.RUNNING,
})

TERMINAL_JOB_STATUSES = frozenset({
    VisualAnalysisJobStatus.COMPLETED,
    VisualAnalysisJobStatus.FAILED,
    VisualAnalysisJobStatus.CANCELED,
})

CANCELLABLE_JOB_STATUSES = frozenset({
    VisualAnalysisJobStatus.QUEUED,
    VisualAnalysisJobStatus.RUNNING,
})


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class VisualAnalysisError(Exception):
    """Base class for visual-analysis errors."""


class VisualAnalysisValidationError(VisualAnalysisError):
    """Hard validation failure (bad input, invalid box, invalid model output)."""


class VisualAnalysisNotFoundError(VisualAnalysisError):
    """A job / artifact / template with the given id does not exist."""


class VisualAnalysisConflictError(VisualAnalysisError):
    """A job-level conflict (already running, wrong state for cancel/retry)."""


class VisualAnalysisUnavailableError(VisualAnalysisError):
    """The vision model / service is not available."""


class VisualAnalysisStorageError(VisualAnalysisError):
    """A persistence failure (corrupt JSON, I/O error)."""


# ---------------------------------------------------------------------------
# Box — normalised rectangle
# ---------------------------------------------------------------------------

class Box(BaseModel):
    """A normalised rectangle.

    All fields are in the inclusive range ``[0, 1]``.  ``x`` / ``y`` is
    the top-left corner; ``width`` / ``height`` extend to the right and
    down.  The box must stay inside the unit square:
    ``x + width <= 1`` and ``y + height <= 1``.
    """

    model_config = ConfigDict(extra="ignore")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _inside_unit_square(self) -> "Box":
        if self.x + self.width > 1.0 + 1e-9:
            raise ValueError(
                f"box x+width ({self.x + self.width}) must be <= 1.0"
            )
        if self.y + self.height > 1.0 + 1e-9:
            raise ValueError(
                f"box y+height ({self.y + self.height}) must be <= 1.0"
            )
        return self

    def iou(self, other: "Box") -> float:
        """Intersection-over-union with *other* (0..1)."""
        ix0 = max(self.x, other.x)
        iy0 = max(self.y, other.y)
        ix1 = min(self.x + self.width, other.x + other.width)
        iy1 = min(self.y + self.height, other.y + other.height)
        iw = max(0.0, ix1 - ix0)
        ih = max(0.0, iy1 - iy0)
        inter = iw * ih
        area_a = self.width * self.height
        area_b = other.width * other.height
        union = area_a + area_b - inter
        if union <= 0.0:
            return 0.0
        return inter / union


# ---------------------------------------------------------------------------
# Keyframe, RegionTrack, LayoutChange
# ---------------------------------------------------------------------------

class Keyframe(BaseModel):
    """A single sampled observation of a region at a point in time."""

    model_config = ConfigDict(extra="ignore")

    time: float = Field(ge=0.0)
    box: Box
    confidence: float = Field(ge=0.0, le=1.0)


class RegionTrack(BaseModel):
    """A tracked region across a time range.

    ``keyframes`` are the sampled observations; the region is assumed to
    stay constant (deterministic hold) between keyframes.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str = Field(description="One of the RegionType values")
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    keyframes: list[Keyframe] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _type_known(cls, value: str) -> str:
        if value not in REGION_TYPES:
            raise ValueError(
                f"unknown region type {value!r}; expected one of {sorted(REGION_TYPES)}"
            )
        return value

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, value: float, info) -> float:
        start = info.data.get("start")
        if start is not None and value < start:
            raise ValueError(
                f"end ({value}) must be >= start ({start})"
            )
        return value

    @field_validator("id")
    @classmethod
    def _id_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("region track id must not be empty")
        return value


class LayoutChange(BaseModel):
    """A detected significant layout change at a point in time."""

    model_config = ConfigDict(extra="ignore")

    time: float = Field(ge=0.0)
    description: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# VisualAnalysisArtifact
# ---------------------------------------------------------------------------

class VisualAnalysisArtifact(BaseModel):
    """The persisted output of a visual analysis run.

    Coordinates are normalised (0..1).  ``source_resolution`` records the
    pixel resolution the analysis was performed at so a consumer can
    scale back to pixels if needed.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    id: str
    media_item_id: str
    source_resolution: list[int] = Field(min_length=2, max_length=2)
    duration_seconds: float = Field(ge=0.0)
    region_tracks: list[RegionTrack] = Field(default_factory=list)
    layout_changes: list[LayoutChange] = Field(default_factory=list)
    created_at: str
    revision: int = 1
    # Origin: "automatic", "template", "manual" or "hybrid".
    origin: str = "automatic"

    @field_validator("source_resolution")
    @classmethod
    def _resolution_positive(cls, value: list[int]) -> list[int]:
        if value[0] <= 0 or value[1] <= 0:
            raise ValueError(
                f"source_resolution must be positive, got {value}"
            )
        return value


# ---------------------------------------------------------------------------
# LayoutTemplate
# ---------------------------------------------------------------------------

class LayoutTemplate(BaseModel):
    """A confirmed layout template for a Twitch profile + resolution.

    Templates are applied first and validated at a few keyframes; on
    significant deviation the service falls back to automatic analysis.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    id: str
    twitch_profile_id: Optional[str] = None
    source_resolution: Optional[list[int]] = None
    name: Optional[str] = None
    region_tracks: list[RegionTrack] = Field(default_factory=list)
    confirmed: bool = False
    created_at: str
    updated_at: str

    @field_validator("source_resolution")
    @classmethod
    def _resolution_positive(cls, value: Optional[list[int]]) -> Optional[list[int]]:
        if value is not None:
            if len(value) != 2 or value[0] <= 0 or value[1] <= 0:
                raise ValueError(
                    f"source_resolution must be [width, height] with positive values, got {value}"
                )
        return value


# ---------------------------------------------------------------------------
# Job record (plain dict persisted as JSON)
# ---------------------------------------------------------------------------

def make_job_record(
    *,
    job_id: str,
    media_item_id: str,
    start_seconds: float,
    end_seconds: float,
    profile_id: Optional[str],
    force: bool,
    manual_regions: list[dict],
    created_at: str,
) -> dict[str, Any]:
    """Build a new job record dict in the QUEUED state."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": job_id,
        "operation": "visual_analysis",
        "media_item_id": media_item_id,
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "profile_id": profile_id,
        "force": force,
        "manual_regions": manual_regions,
        "status": VisualAnalysisJobStatus.QUEUED,
        "progress": 0.0,
        "current_stage": None,
        "created_at": created_at,
        "started_at": None,
        "completed_at": None,
        "output_artifact_id": None,
        "template_id": None,
        "origin": None,
        "error": None,
    }
