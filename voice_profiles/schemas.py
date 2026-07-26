"""Schemas, enums and typed errors for the voice-profile backend core.

This module is the single source of truth for the JSON shape written to
``voice_profiles_data/{profile_id}/profile.json``. It deliberately mirrors
the conventions of :mod:`voice_clone.schemas` (pydantic v2 models, string
enums, hard constants) but does not import anything from the voice-clone
vertical slice: the voice-profile core must stay isolated.
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

SUPPORTED_LOCALES: frozenset[str] = frozenset({"de-DE"})
DEFAULT_LOCALE = "de-DE"

MAX_PROFILE_NAME_LEN = 80
EXPECTED_PACK_PROMPT_COUNT = 88


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ReferenceStatus(str, Enum):
    """Status of a reference attached to a profile."""

    ACCEPTED = "ACCEPTED"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"


class QualityClass(str, Enum):
    """Technical quality class reported by the voice-clone analyzer."""

    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


# Mapping from a technical quality class to the canonical reference status.
QUALITY_CLASS_TO_STATUS: dict[QualityClass, ReferenceStatus] = {
    QualityClass.EXCELLENT: ReferenceStatus.ACCEPTED,
    QualityClass.GOOD: ReferenceStatus.ACCEPTED,
    QualityClass.REVIEW: ReferenceStatus.REVIEW,
    QualityClass.REJECT: ReferenceStatus.REJECTED,
}


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class VoiceProfileError(Exception):
    """Base class for all voice-profile core errors."""


class VoiceProfileValidationError(VoiceProfileError):
    """Hard validation failure (bad name, bad filename, bad quality, ...)."""


class VoiceProfileNotFoundError(VoiceProfileError):
    """A profile with the given id does not exist."""


class VoiceProfileConflictError(VoiceProfileError):
    """A profile-level conflict (e.g. trying to accept a REJECTED reference)."""


class VoiceScriptNotFoundError(VoiceProfileError):
    """A script id was not found in the loaded script library."""


class VoiceProfileStorageError(VoiceProfileError):
    """A persistence-layer failure (corrupt JSON, unknown schema, IO)."""


# ---------------------------------------------------------------------------
# Script library models
# ---------------------------------------------------------------------------

class RecommendedDuration(BaseModel):
    min: float
    max: float


class ScriptPrompt(BaseModel):
    """A single recording prompt loaded from the script library."""

    id: str
    order: int
    category: str
    style: str
    text: str
    recommended_duration_seconds: RecommendedDuration
    tags: list[str] = Field(default_factory=list)
    recording_notes: Optional[str] = None


class ScriptPack(BaseModel):
    """Top-level shape of a pack/holdout JSON file."""

    schema_version: int
    id: str
    locale: str
    name: str
    version: str
    expected_prompt_count: Optional[int] = None
    prompts: list[ScriptPrompt]


# ---------------------------------------------------------------------------
# Voice-profile models
# ---------------------------------------------------------------------------

class Progress(BaseModel):
    """Derived progress for a profile. Never persisted as a stale counter."""

    total: int
    missing: int
    recorded: int
    accepted: int
    review: int
    rejected: int
    percentage: float
    clone_ready: bool
    pack_complete: bool


class Reference(BaseModel):
    """A reference attached to a profile, keyed by stable script id."""

    script_id: str
    script_text: str
    category: str
    style: str
    recording_filename: str
    recording_sha256: str
    quality: dict[str, Any] = Field(default_factory=dict)
    quality_class: QualityClass
    status: ReferenceStatus
    review_accepted: bool = False
    attached_at: str
    updated_at: str


class Profile(BaseModel):
    """A persisted voice profile."""

    schema_version: int = SCHEMA_VERSION
    id: str
    name: str
    locale: str
    created_at: str
    updated_at: str
    archived: bool = False
    references: dict[str, Reference] = Field(default_factory=dict)
