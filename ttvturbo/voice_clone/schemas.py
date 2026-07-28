"""Pydantic schemas and constants for the voice-clone vertical slice.

These models are the single source of truth for the JSON shape written to
``voice_clones/{id}/metadata.json`` and returned by the REST API.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# Status values are part of the public API contract and must match the
# frontend exactly. Do not rename without coordinating both sides.
class GenerationStatus(str, Enum):
    QUEUED = "QUEUED"
    VALIDATING_REFERENCE = "VALIDATING_REFERENCE"
    LOADING_MODEL = "LOADING_MODEL"
    GENERATING = "GENERATING"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    READY = "READY"
    FAILED = "FAILED"


# Hard limits shared with the spike diagnostics. Kept in sync on purpose.
MAX_TARGET_CHARS = 300
MIN_REF_SECONDS = 2.0
MAX_REF_SECONDS = 30.0
RECOMMENDED_REF_MIN = 5.0
RECOMMENDED_REF_MAX = 12.0
MIN_OUTPUT_SECONDS = 0.5

MODEL_ID_DEFAULT = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
DEVICE_DEFAULT = "cuda:0"
DTYPE_DEFAULT = "bfloat16"
LANGUAGE_DEFAULT = "German"


class CreateGenerationRequest(BaseModel):
    # Manual (legacy) mode: the client supplies the reference recording and
    # the exact reference text. Either both are set (manual mode) or neither
    # is set (profile mode). Mixing the two modes is rejected by the service.
    reference_recording: str = Field(
        "", description="Filename inside the recordings directory (manual mode)."
    )
    reference_text: str = Field(
        "", description="Exact transcript of the reference audio (manual mode)."
    )
    target_text: str = Field(..., description="Target text to synthesize (max 300 chars).")
    language: str = Field(LANGUAGE_DEFAULT, description="Synthesis language.")
    allow_quality_warning: bool = Field(
        False,
        description="If true, proceed even when the reference quality is REVIEW.",
    )
    # Profile mode: the client only picks a profile + accepted reference
    # script id. The server resolves the WAV and the script text; the client
    # cannot override either.
    voice_profile_id: Optional[str] = Field(
        None, description="Voice profile id (profile mode)."
    )
    voice_profile_script_id: Optional[str] = Field(
        None, description="Script id of an accepted reference on the profile (profile mode)."
    )


class CreateGenerationResponse(BaseModel):
    id: str
    status: GenerationStatus


class GenerationMetadata(BaseModel):
    """On-disk metadata shape. Persisted atomically per generation."""

    id: str
    status: GenerationStatus
    reference_recording: str
    reference_sha256: str = ""
    reference_text: str
    target_text: str
    language: str = LANGUAGE_DEFAULT
    model_id: str = MODEL_ID_DEFAULT
    model_revision: str = "unknown"
    created_at: str
    completed_at: Optional[str] = None
    output_duration_seconds: Optional[float] = None
    output_sample_rate: Optional[int] = None
    output_file_size_bytes: Optional[int] = None
    output_sha256: Optional[str] = None
    generation_seconds: Optional[float] = None
    peak_vram_bytes: Optional[int] = None
    attention_backend: Optional[str] = None
    worker_exit_code: Optional[int] = None
    quality: dict[str, Any] = Field(default_factory=dict)
    failure_reason: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class GenerationListResponse(BaseModel):
    generations: list[GenerationMetadata]


class VoiceCloneStatusResponse(BaseModel):
    available: bool
    busy: bool
    active_generation_id: Optional[str] = None
    model_id: str = MODEL_ID_DEFAULT
    # New diagnostic fields (additive only; the frontend ignores unknown
    # keys). They describe the real GPU/runtime availability, never a
    # hard-coded value.
    device: Optional[str] = None
    python_version: Optional[str] = None
    torch_version: Optional[str] = None
    torch_cuda_version: Optional[str] = None
    cuda_available: bool = False
    device_name: Optional[str] = None
    vram_total_bytes: Optional[int] = None
    vram_free_bytes: Optional[int] = None
    qwen_tts_importable: bool = False
    soundfile_ok: bool = False
    ffmpeg_ok: bool = False
    data_dir_writable: bool = False
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
