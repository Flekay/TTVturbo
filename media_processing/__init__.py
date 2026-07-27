"""TTVturbo shared media-processing core.

Reusable foundation for audio extraction, transcription and the VOD
pipeline orchestration. No FastAPI, no React imports. The FastAPI
integration lives in :mod:`media_processing_api` at the repo root.

Design rules (see project spec):

* the pipeline module orchestrates the same services the on-demand pages
  use — it never re-implements download, audio or transcription logic;
* only ``source_type = "twitch_vod"`` is supported in this phase, but
  the source resolver is the single extension point;
* a project-wide cross-process GPU lock (see :mod:`media_processing.gpu_lock`)
  is shared between Qwen3-TTS voice-clone and faster-whisper transcription;
* all persistence is atomic JSON (tmp + os.replace) with UUID validation
  and path-traversal protection, mirroring :mod:`vod_pipeline.storage`.
"""

from __future__ import annotations

from .schemas import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    AudioArtifactMetadata,
    JobType,
    MediaJob,
    MediaJobError,
    MediaJobNotFoundError,
    MediaJobConflictError,
    MediaJobValidationError,
    MediaJobStorageError,
    MediaSourceError,
    MediaSourceNotFoundError,
    MediaSourceNotReadyError,
    MediaProgress,
    PipelineRun,
    PipelineRunError,
    PipelineRunNotFoundError,
    PipelineRunConflictError,
    PipelineRunStorageError,
    PipelineRunValidationError,
    PipelineStatus,
    PipelineStep,
    PipelineStepStatus,
    PipelineStepType,
    TranscriptionMetadata,
    TranscriptionSegment,
    TranscriptionWord,
    TranscriptionStatus,
)
from .storage import MediaJobStorage
from .gpu_lock import GpuLock, GpuLockBusyError, GpuLockError, GpuLockOwner
from .sources import MediaSourceResolver, ResolvedMediaSource
from .audio_extraction import AudioExtractionService, AudioExtractionError
from .transcription import TranscriptionService, TranscriptionError
from .pipeline import PipelineService, PipelineError
from .uploads import UploadStorage, UploadNotFoundError, UploadStorageError

__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "AudioArtifactMetadata",
    "AudioExtractionError",
    "AudioExtractionService",
    "GpuLock",
    "GpuLockBusyError",
    "GpuLockError",
    "GpuLockOwner",
    "JobType",
    "MediaJob",
    "MediaJobConflictError",
    "MediaJobError",
    "MediaJobNotFoundError",
    "MediaJobStorageError",
    "MediaJobValidationError",
    "MediaJobStorage",
    "MediaProgress",
    "MediaSourceError",
    "MediaSourceNotFoundError",
    "MediaSourceNotReadyError",
    "MediaSourceResolver",
    "MediaSourceResolver",
    "PipelineError",
    "PipelineRun",
    "PipelineRunConflictError",
    "PipelineRunError",
    "PipelineRunNotFoundError",
    "PipelineRunStorageError",
    "PipelineRunValidationError",
    "PipelineService",
    "PipelineStatus",
    "PipelineStep",
    "PipelineStepStatus",
    "PipelineStepType",
    "ResolvedMediaSource",
    "TranscriptionError",
    "TranscriptionMetadata",
    "TranscriptionSegment",
    "TranscriptionService",
    "TranscriptionStatus",
    "TranscriptionWord",
    "UploadNotFoundError",
    "UploadStorage",
    "UploadStorageError",
]
