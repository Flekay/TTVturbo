"""TTVturbo Visual Analysis backend capability.

Reusable detection of gameplay, facecam, chat and overlay regions in a
video or selected time range.  No UI, no rendering.

Public API
----------
* :class:`VisualAnalysisService` — orchestrates jobs, artifacts and
  layout templates.
* :class:`VisualAnalysisStorage` — atomic JSON persistence.
* :class:`VisionAdapter` (Protocol) + :class:`StaticVisionAdapter` /
  :class:`UnavailableVisionAdapter` — vision model integration points.
* Schemas (:class:`Box`, :class:`Keyframe`, :class:`RegionTrack`,
  :class:`LayoutChange`, :class:`VisualAnalysisArtifact`,
  :class:`LayoutTemplate`, :class:`RegionType`).
* Tracking helpers (:func:`track_regions`, :func:`detect_layout_changes`,
  :func:`validate_model_output`).
"""

from __future__ import annotations

from .schemas import (
    SCHEMA_VERSION,
    Box,
    Keyframe,
    LayoutChange,
    LayoutTemplate,
    RegionTrack,
    RegionType,
    REGION_TYPES,
    VisualAnalysisArtifact,
    VisualAnalysisConflictError,
    VisualAnalysisError,
    VisualAnalysisJobStatus,
    VisualAnalysisNotFoundError,
    VisualAnalysisStorageError,
    VisualAnalysisUnavailableError,
    VisualAnalysisValidationError,
    ACTIVE_JOB_STATUSES,
    CANCELLABLE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from .storage import VisualAnalysisStorage
from .tracking import (
    DEFAULT_MATCH_IOU,
    KeyframeResult,
    detect_layout_changes,
    track_regions,
    validate_detected_region,
    validate_model_output,
    validate_template_against_keyframes,
)
from .vision import (
    DetectedRegion,
    StaticVisionAdapter,
    UnavailableVisionAdapter,
    VisionAdapter,
    detected_region_from_dict,
)
from .service import (
    ARTIFACT_TYPE,
    OPERATION,
    VisualAnalysisService,
)

__all__ = [
    "SCHEMA_VERSION",
    "ARTIFACT_TYPE",
    "OPERATION",
    "Box",
    "Keyframe",
    "LayoutChange",
    "LayoutTemplate",
    "RegionTrack",
    "RegionType",
    "REGION_TYPES",
    "VisualAnalysisArtifact",
    "VisualAnalysisConflictError",
    "VisualAnalysisError",
    "VisualAnalysisJobStatus",
    "VisualAnalysisNotFoundError",
    "VisualAnalysisStorageError",
    "VisualAnalysisUnavailableError",
    "VisualAnalysisValidationError",
    "ACTIVE_JOB_STATUSES",
    "CANCELLABLE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "VisualAnalysisService",
    "VisualAnalysisStorage",
    "DetectedRegion",
    "StaticVisionAdapter",
    "UnavailableVisionAdapter",
    "VisionAdapter",
    "detected_region_from_dict",
    "DEFAULT_MATCH_IOU",
    "KeyframeResult",
    "detect_layout_changes",
    "track_regions",
    "validate_detected_region",
    "validate_model_output",
    "validate_template_against_keyframes",
]
