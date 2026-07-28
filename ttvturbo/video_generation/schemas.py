"""Schemas for the Video Generation backend capability.

Pure pydantic models, enums and typed errors. No FastAPI, no I/O, no
heavy domain imports.  The video-generation backend produces real
generated videos (no simulated results) via a concrete local model
adapter (see :mod:`.worker`).

Generation types
----------------
* ``TEXT_TO_VIDEO`` -- prompt-only generation.
* ``IMAGE_TO_VIDEO`` -- prompt + a library image asset as the first frame.

``LIPSYNC`` and ``AVATAR_VIDEO`` are listed in
:data:`OPTIONAL_GENERATION_TYPES` but are only advertised in
:func:`capabilities` when the operator explicitly enables them via
settings **and** a real adapter is wired.  In this phase they are not
implemented and never advertised by default.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Generation types
# ---------------------------------------------------------------------------

class GenerationType(str, Enum):
    """Supported video-generation types."""

    TEXT_TO_VIDEO = "TEXT_TO_VIDEO"
    IMAGE_TO_VIDEO = "IMAGE_TO_VIDEO"
    LIPSYNC = "LIPSYNC"
    AVATAR_VIDEO = "AVATAR_VIDEO"


# Types accepted by :meth:`VideoGenerationService.start_job` in this phase.
SUPPORTED_GENERATION_TYPES: frozenset[str] = frozenset({
    GenerationType.TEXT_TO_VIDEO.value,
    GenerationType.IMAGE_TO_VIDEO.value,
})

# Optional types that are only advertised when explicitly enabled by the
# operator AND a real adapter is wired.  Never advertised by default.
OPTIONAL_GENERATION_TYPES: frozenset[str] = frozenset({
    GenerationType.LIPSYNC.value,
    GenerationType.AVATAR_VIDEO.value,
})


# ---------------------------------------------------------------------------
# Aspect ratios + resolution whitelist
# ---------------------------------------------------------------------------

class AspectRatio(str, Enum):
    """Whitelisted aspect ratios."""

    RATIO_16_9 = "16:9"
    RATIO_9_16 = "9:16"
    RATIO_1_1 = "1:1"
    RATIO_4_3 = "4:3"
    RATIO_3_4 = "3:4"


ASPECT_RATIOS: frozenset[str] = frozenset(r.value for r in AspectRatio)

# Concrete pixel resolutions per aspect ratio.  These are the only
# resolutions the backend will emit; the model adapter is responsible for
# producing (or rescaling to) exactly these dimensions.  Whitelisting
# happens here, once, so neither the API nor the worker re-implements it.
RESOLUTIONS_BY_ASPECT_RATIO: dict[str, tuple[int, int]] = {
    "16:9": (720, 480),
    "9:16": (480, 720),
    "1:1": (480, 480),
    "4:3": (640, 480),
    "3:4": (480, 640),
}

# Whitelist of allowed [width, height] pairs (derived from the map above).
WHITELISTED_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset(
    RESOLUTIONS_BY_ASPECT_RATIO.values()
)


def resolution_for_aspect_ratio(aspect_ratio: str) -> tuple[int, int]:
    """Return the whitelisted ``(width, height)`` for *aspect_ratio*.

    Raises :class:`VideoGenerationValidationError` for unknown ratios.
    """
    res = RESOLUTIONS_BY_ASPECT_RATIO.get(aspect_ratio)
    if res is None:
        raise VideoGenerationValidationError(
            f"unknown aspect_ratio {aspect_ratio!r}; "
            f"expected one of {sorted(ASPECT_RATIOS)}"
        )
    return res


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

class VideoGenerationJobStatus:
    """Lifecycle status of a video-generation job."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


ACTIVE_JOB_STATUSES = frozenset({
    VideoGenerationJobStatus.QUEUED,
    VideoGenerationJobStatus.RUNNING,
})

TERMINAL_JOB_STATUSES = frozenset({
    VideoGenerationJobStatus.COMPLETED,
    VideoGenerationJobStatus.FAILED,
    VideoGenerationJobStatus.CANCELED,
})

CANCELLABLE_JOB_STATUSES = frozenset({
    VideoGenerationJobStatus.QUEUED,
    VideoGenerationJobStatus.RUNNING,
})


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class VideoGenerationError(Exception):
    """Base class for video-generation errors."""


class VideoGenerationValidationError(VideoGenerationError):
    """Hard validation failure (bad input, invalid type, bad duration)."""


class VideoGenerationNotFoundError(VideoGenerationError):
    """A job / artifact with the given id does not exist."""


class VideoGenerationConflictError(VideoGenerationError):
    """A job-level conflict (already running, wrong state for cancel/retry)."""


class VideoGenerationUnavailableError(VideoGenerationError):
    """The generation model / service is not available."""


class VideoGenerationStorageError(VideoGenerationError):
    """A persistence failure (corrupt JSON, I/O error)."""


# ---------------------------------------------------------------------------
# Effective options (validated, bounded)
# ---------------------------------------------------------------------------

class EffectiveOptions(BaseModel):
    """The effective, validated options actually applied to a generation.

    Free-form ``options`` from the request is filtered down to a small,
    whitelisted set with bounded values.  The effective options are
    stored on the job and the artifact so every generation is
    reproducibly documented.
    """

    model_config = ConfigDict(extra="ignore")

    num_frames: int = Field(default=49, ge=9, le=81)
    guidance_scale: float = Field(default=6.0, ge=0.0, le=30.0)
    num_inference_steps: int = Field(default=50, ge=1, le=200)
    negative_prompt: str = Field(default="", max_length=1000)


# ---------------------------------------------------------------------------
# Generation result (written by the worker, read by the service)
# ---------------------------------------------------------------------------

class GenerationResult(BaseModel):
    """The worker-written result of a successful generation.

    The worker writes ``result.json`` next to the output video; the
    service reads it to build the artifact and register the library
    item.  Fields are strictly validated so a buggy worker cannot
    register a malformed artifact.
    """

    model_config = ConfigDict(extra="ignore")

    success: bool
    model_id: str
    model_revision: Optional[str] = None
    prompt: str
    seed: int
    duration_seconds: float = Field(ge=0.0)
    resolution: list[int] = Field(min_length=2, max_length=2)
    fps: int = Field(ge=1, le=120)
    file_name: str
    file_size_bytes: int = Field(ge=0)
    effective_options: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

    @field_validator("resolution")
    @classmethod
    def _resolution_whitelisted(cls, value: list[int]) -> list[int]:
        pair = (int(value[0]), int(value[1]))
        if pair not in WHITELISTED_RESOLUTIONS:
            raise ValueError(
                f"resolution {value} is not whitelisted; "
                f"expected one of {sorted(WHITELISTED_RESOLUTIONS)}"
            )
        return [int(value[0]), int(value[1])]


# ---------------------------------------------------------------------------
# Artifact (persisted)
# ---------------------------------------------------------------------------

class VideoGenerationArtifact(BaseModel):
    """The persisted output artifact of a completed generation.

    A failed generation is **never** registered as an artifact (see
    project spec).  The artifact references the library item that owns
    the generated video file.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    id: str
    job_id: str
    generation_type: str
    model_id: str
    model_revision: Optional[str] = None
    prompt: str
    seed: int
    effective_options: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = Field(ge=0.0)
    resolution: list[int] = Field(min_length=2, max_length=2)
    fps: int = Field(ge=1, le=120)
    library_item_id: str
    file_name: str
    file_size_bytes: int = Field(ge=0)
    container: str = "mp4"
    source_image_asset_id: Optional[str] = None
    created_at: str
    revision: int = 1

    @field_validator("generation_type")
    @classmethod
    def _type_known(cls, value: str) -> str:
        known = SUPPORTED_GENERATION_TYPES | OPTIONAL_GENERATION_TYPES
        if value not in known:
            raise ValueError(
                f"unknown generation type {value!r}; expected one of {sorted(known)}"
            )
        return value

    @field_validator("resolution")
    @classmethod
    def _resolution_whitelisted(cls, value: list[int]) -> list[int]:
        pair = (int(value[0]), int(value[1]))
        if pair not in WHITELISTED_RESOLUTIONS:
            raise ValueError(
                f"resolution {value} is not whitelisted; "
                f"expected one of {sorted(WHITELISTED_RESOLUTIONS)}"
            )
        return [int(value[0]), int(value[1])]


# ---------------------------------------------------------------------------
# Job record (plain dict persisted as JSON)
# ---------------------------------------------------------------------------

def make_job_record(
    *,
    job_id: str,
    generation_type: str,
    prompt: str,
    source_image_asset_id: Optional[str],
    duration_seconds: float,
    aspect_ratio: str,
    seed: Optional[int],
    options: dict[str, Any],
    effective_options: dict[str, Any],
    resolution: list[int],
    fps: int,
    model_id: str,
    created_at: str,
) -> dict[str, Any]:
    """Build a new job record dict in the QUEUED state."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": job_id,
        "operation": "video_generation",
        "type": generation_type,
        "prompt": prompt,
        "source_image_asset_id": source_image_asset_id,
        "duration_seconds": float(duration_seconds),
        "aspect_ratio": aspect_ratio,
        "seed": seed,
        "options": options,
        "effective_options": effective_options,
        "resolution": [int(resolution[0]), int(resolution[1])],
        "fps": int(fps),
        "model": {"model_id": model_id, "model_revision": None},
        "status": VideoGenerationJobStatus.QUEUED,
        "progress": 0.0,
        "current_stage": None,
        "created_at": created_at,
        "started_at": None,
        "completed_at": None,
        "output_artifact_id": None,
        "library_item_id": None,
        "worker_pid": None,
        "error": None,
    }
