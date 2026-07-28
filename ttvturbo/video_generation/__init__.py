"""TTVturbo Video Generation backend capability.

A reusable, on-demand video-generation API for Synthetic Studio and
later pipelines.  No UI, no rendering.  Produces real generated videos
via a concrete local model adapter (diffusers CogVideoX, see
:mod:`.worker`).

Public API
----------
* :class:`VideoGenerationService` -- orchestrates jobs, artifacts and
  library registration.
* :class:`VideoGenerationStorage` -- atomic JSON persistence.
* :class:`VideoGenerationAdapter` (Protocol) +
  :class:`UnavailableVideoGenerationAdapter` -- availability / capability
  reporting integration points.
* Schemas (:class:`GenerationType`, :class:`AspectRatio`,
  :class:`EffectiveOptions`, :class:`GenerationResult`,
  :class:`VideoGenerationArtifact`, :class:`VideoGenerationJobStatus`).
"""

from __future__ import annotations

from .schemas import (
    SCHEMA_VERSION,
    ACTIVE_JOB_STATUSES,
    ASPECT_RATIOS,
    AspectRatio,
    CANCELLABLE_JOB_STATUSES,
    EffectiveOptions,
    GenerationType,
    GenerationResult,
    OPTIONAL_GENERATION_TYPES,
    RESOLUTIONS_BY_ASPECT_RATIO,
    SUPPORTED_GENERATION_TYPES,
    TERMINAL_JOB_STATUSES,
    VideoGenerationArtifact,
    VideoGenerationConflictError,
    VideoGenerationError,
    VideoGenerationJobStatus,
    VideoGenerationNotFoundError,
    VideoGenerationStorageError,
    VideoGenerationUnavailableError,
    VideoGenerationValidationError,
    WHITELISTED_RESOLUTIONS,
    make_job_record,
    resolution_for_aspect_ratio,
)
from .storage import VideoGenerationStorage
from .adapter import (
    UnavailableVideoGenerationAdapter,
    VideoGenerationAdapter,
    raise_unavailable,
)
from .service import (
    ARTIFACT_TYPE,
    OPERATION,
    VideoGenerationService,
)

__all__ = [
    "SCHEMA_VERSION",
    "ARTIFACT_TYPE",
    "OPERATION",
    "ACTIVE_JOB_STATUSES",
    "ASPECT_RATIOS",
    "AspectRatio",
    "CANCELLABLE_JOB_STATUSES",
    "EffectiveOptions",
    "GenerationType",
    "GenerationResult",
    "OPTIONAL_GENERATION_TYPES",
    "RESOLUTIONS_BY_ASPECT_RATIO",
    "SUPPORTED_GENERATION_TYPES",
    "TERMINAL_JOB_STATUSES",
    "VideoGenerationArtifact",
    "VideoGenerationConflictError",
    "VideoGenerationError",
    "VideoGenerationJobStatus",
    "VideoGenerationNotFoundError",
    "VideoGenerationService",
    "VideoGenerationStorage",
    "VideoGenerationStorageError",
    "VideoGenerationUnavailableError",
    "VideoGenerationValidationError",
    "VideoGenerationAdapter",
    "UnavailableVideoGenerationAdapter",
    "WHITELISTED_RESOLUTIONS",
    "make_job_record",
    "raise_unavailable",
    "resolution_for_aspect_ratio",
]
