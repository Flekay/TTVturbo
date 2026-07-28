"""Schemas, enums and typed errors for the shared media-processing core.

Single source of truth for the JSON shapes written to
``media_jobs/{job_id}/job.json`` and ``pipeline_runs/{run_id}/run.json``.
Mirrors the conventions of :mod:`vod_pipeline.schemas` but stays
isolated from it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


# ---------------------------------------------------------------------------
# Media job
# ---------------------------------------------------------------------------

class JobType(str, Enum):
    EXTRACT_AUDIO = "EXTRACT_AUDIO"
    TRANSCRIBE = "TRANSCRIBE"


class MediaJobStatus(str, Enum):
    QUEUED = "QUEUED"
    WAITING_FOR_DEPENDENCY = "WAITING_FOR_DEPENDENCY"
    WAITING_FOR_GPU = "WAITING_FOR_GPU"
    RUNNING = "RUNNING"
    EXPORTING = "EXPORTING"
    READY = "READY"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


# Transient states: if the worker exits while in one of these, the job is
# automatically marked FAILED by the reaper / startup recovery.
TRANSIENT_JOB_STATUSES = frozenset({
    MediaJobStatus.QUEUED,
    MediaJobStatus.WAITING_FOR_DEPENDENCY,
    MediaJobStatus.WAITING_FOR_GPU,
    MediaJobStatus.RUNNING,
    MediaJobStatus.EXPORTING,
})

# Statuses that may be canceled.
CANCELLABLE_JOB_STATUSES = frozenset({
    MediaJobStatus.QUEUED,
    MediaJobStatus.WAITING_FOR_DEPENDENCY,
    MediaJobStatus.WAITING_FOR_GPU,
    MediaJobStatus.RUNNING,
    MediaJobStatus.EXPORTING,
})

# Statuses from which a job may be retried.
RETRYABLE_JOB_STATUSES = frozenset({
    MediaJobStatus.FAILED,
    MediaJobStatus.CANCELED,
})


# Optional phase hint written by workers into the job result/progress so the
# frontend can show "Modell wird geladen" vs "Transkribiert" honestly.
class TranscriptionPhase(str, Enum):
    WAITING_FOR_GPU = "WAITING_FOR_GPU"
    LOADING_MODEL = "LOADING_MODEL"
    TRANSCRIBING = "TRANSCRIBING"
    EXPORTING = "EXPORTING"


class MediaProgress(BaseModel):
    percent: Optional[float] = None
    processed_seconds: Optional[float] = None
    total_seconds: Optional[float] = None
    phase: Optional[str] = None  # TranscriptionPhase value or None


class MediaJob(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    job_type: str  # JobType value
    source_type: str  # "twitch_vod"
    source_id: str  # VOD uuid
    status: str = MediaJobStatus.QUEUED.value
    progress: MediaProgress = Field(default_factory=MediaProgress)
    options: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    # Link to a dependency job (e.g. transcription depends on audio extraction).
    depends_on: Optional[str] = None
    # Linked transcription id (for TRANSCRIBE jobs that produce a transcript).
    transcription_id: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: str


# ---------------------------------------------------------------------------
# Audio artifact metadata (vods/{vod_id}/artifacts/audio/metadata.json)
# ---------------------------------------------------------------------------

class AudioArtifactMetadata(BaseModel):
    schema_version: int = SCHEMA_VERSION
    source_type: str
    source_id: str
    file_name: str  # "source_audio.flac"
    container: str = "flac"
    sample_rate: int = 16000
    channels: int = 1
    codec: Optional[str] = None
    duration_seconds: float
    file_size_bytes: int
    sha256: str
    created_at: str
    # The job that produced this artifact (provenance).
    produced_by_job_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

class TranscriptionStatus(str, Enum):
    """Lifecycle status of a persisted transcription record.

    Mirrors the media job status but is owned by the transcription record
    itself so the transcript remains queryable after the job is gone.
    """
    QUEUED = "QUEUED"
    WAITING_FOR_AUDIO = "WAITING_FOR_AUDIO"
    WAITING_FOR_GPU = "WAITING_FOR_GPU"
    RUNNING = "RUNNING"
    EXPORTING = "EXPORTING"
    READY = "READY"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class TranscriptionWord(BaseModel):
    start: float
    end: float
    text: str
    probability: Optional[float] = None


class TranscriptionSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    avg_logprob: Optional[float] = None
    no_speech_probability: Optional[float] = None
    words: list[TranscriptionSegment] = Field(default_factory=list)  # type: ignore[assignment]
    # NOTE: the words field is typed loosely on purpose; the worker writes
    # TranscriptionWord dicts. The model is only used for validation in
    # tests; the persisted JSON is the source of truth.


class TranscriptionMetadata(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    source_type: str
    source_id: str
    audio_artifact: str  # relative path within vod dir
    model: str
    device: str
    compute_type: str
    language: Optional[str] = None
    language_probability: Optional[float] = None
    duration_seconds: float
    created_at: str
    segments: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline run
# ---------------------------------------------------------------------------

class PipelineStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_GPU = "WAITING_FOR_GPU"
    CANCELING = "CANCELING"
    RETRYING = "RETRYING"
    READY_FOR_CLIP_ANALYSIS = "READY_FOR_CLIP_ANALYSIS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


# Active (non-terminal) run statuses — polled by the UI "Aktiv" tab.
ACTIVE_PIPELINE_STATUSES = frozenset({
    PipelineStatus.QUEUED.value,
    PipelineStatus.RUNNING.value,
    PipelineStatus.WAITING_FOR_GPU.value,
    PipelineStatus.CANCELING.value,
    PipelineStatus.RETRYING.value,
})

# Terminal run statuses — shown in the "Verlauf" tab and deletable.
TERMINAL_PIPELINE_STATUSES = frozenset({
    PipelineStatus.COMPLETED.value,
    PipelineStatus.READY_FOR_CLIP_ANALYSIS.value,
    PipelineStatus.FAILED.value,
    PipelineStatus.CANCELED.value,
})

# Statuses from which a run may be canceled.
CANCELLABLE_PIPELINE_STATUSES = frozenset({
    PipelineStatus.QUEUED.value,
    PipelineStatus.RUNNING.value,
    PipelineStatus.WAITING_FOR_GPU.value,
    PipelineStatus.RETRYING.value,
})


class PipelineStepType(str, Enum):
    RESOLVE_SOURCE = "RESOLVE_SOURCE"
    DOWNLOAD = "DOWNLOAD"
    EXTRACT_AUDIO = "EXTRACT_AUDIO"
    TRANSCRIBE = "TRANSCRIBE"
    CONVERSATION_MINING = "CONVERSATION_MINING"
    FIND_CLIPS = "FIND_CLIPS"


class PipelineStepStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    WAITING_FOR_GPU = "WAITING_FOR_GPU"
    READY = "READY"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELED = "CANCELED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


# Step statuses that count as "done" for orchestration purposes.
DONE_STEP_STATUSES = frozenset({
    PipelineStepStatus.READY.value,
    PipelineStepStatus.SKIPPED.value,
    PipelineStepStatus.NOT_IMPLEMENTED.value,
})


class PipelineStep(BaseModel):
    type: str  # PipelineStepType value
    status: str = PipelineStepStatus.WAITING.value
    job_id: Optional[str] = None
    error: Optional[str] = None
    # Additive fields (v2 runs). Old runs normalize these to defaults.
    progress: Optional[float] = None
    message: Optional[str] = None
    attempt: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    artifact_ids: list[str] = Field(default_factory=list)


class PipelineRun(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    source_type: str
    source_id: str
    profile_id: Optional[str] = None
    status: str = PipelineStatus.QUEUED.value
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None
    # Additive fields (v2 runs). Old runs normalize these to None.
    source: Optional[dict[str, Any]] = None
    progress: Optional[float] = None
    current_step: Optional[str] = None
    started_at: Optional[str] = None
    library_item_id: Optional[str] = None
    transcript_id: Optional[str] = None


# Fixed progress weights per step (sum = 100). Used for the overall run
# progress so a long download is not equal to a quick metadata resolve.
PIPELINE_STEP_WEIGHTS: dict[str, float] = {
    PipelineStepType.RESOLVE_SOURCE.value: 5.0,
    PipelineStepType.DOWNLOAD.value: 45.0,
    PipelineStepType.EXTRACT_AUDIO.value: 13.0,
    PipelineStepType.TRANSCRIBE.value: 27.0,
    PipelineStepType.CONVERSATION_MINING.value: 10.0,
}


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class MediaJobError(Exception):
    """Base class for media-job errors."""


class MediaJobValidationError(MediaJobError):
    """Hard validation failure (bad source, bad state transition)."""


class MediaJobNotFoundError(MediaJobError):
    """A job with the given id does not exist."""


class MediaJobConflictError(MediaJobError):
    """A job-level conflict (already running, wrong state)."""


class MediaJobStorageError(MediaJobError):
    """A persistence-layer failure (corrupt JSON, unknown schema, IO)."""


class MediaSourceError(Exception):
    """Base class for media-source resolution errors."""


class MediaSourceNotFoundError(MediaSourceError):
    """A source with the given id does not exist."""


class MediaSourceNotReadyError(MediaSourceError):
    """The source exists but is not READY (e.g. VOD not downloaded)."""


class PipelineRunError(Exception):
    """Base class for pipeline-run errors."""


class PipelineRunValidationError(PipelineRunError):
    """Hard validation failure."""


class PipelineRunUnavailableError(PipelineRunError):
    """A required runtime precondition is missing (e.g. mining model)."""


class PipelineRunNotFoundError(PipelineRunError):
    """A pipeline run with the given id does not exist."""


class PipelineRunConflictError(PipelineRunError):
    """A pipeline-run conflict (already running, wrong state)."""


class PipelineRunStorageError(PipelineRunError):
    """A persistence-layer failure."""
