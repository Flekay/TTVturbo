"""TTVturbo voice-profile backend core.

Isolated module that manages voice profiles, their recording-pack progress,
references to existing WAV recordings, technical quality results, and
atomar file-based persistence. No FastAPI, no React, no Qwen3-TTS imports.
"""

from __future__ import annotations

from .library import (
    DEFAULT_HOLDOUT_PATH,
    DEFAULT_PACK_PATH,
    ScriptLibrary,
)
from .schemas import (
    DEFAULT_LOCALE,
    EXPECTED_PACK_PROMPT_COUNT,
    MAX_PROFILE_NAME_LEN,
    Progress,
    QualityClass,
    QUALITY_CLASS_TO_STATUS,
    Reference,
    ReferenceStatus,
    SCHEMA_VERSION,
    SUPPORTED_LOCALES,
    SUPPORTED_SCHEMA_VERSIONS,
    ScriptPrompt,
    VoiceProfileConflictError,
    VoiceProfileError,
    VoiceProfileNotFoundError,
    VoiceProfileStorageError,
    VoiceProfileValidationError,
    VoiceScriptNotFoundError,
)
from .service import VoiceProfileService
from .storage import VoiceProfileStorage

__all__ = [
    "DEFAULT_HOLDOUT_PATH",
    "DEFAULT_LOCALE",
    "DEFAULT_PACK_PATH",
    "EXPECTED_PACK_PROMPT_COUNT",
    "MAX_PROFILE_NAME_LEN",
    "QUALITY_CLASS_TO_STATUS",
    "Progress",
    "QualityClass",
    "Reference",
    "ReferenceStatus",
    "SCHEMA_VERSION",
    "ScriptLibrary",
    "ScriptPrompt",
    "SUPPORTED_LOCALES",
    "SUPPORTED_SCHEMA_VERSIONS",
    "VoiceProfileConflictError",
    "VoiceProfileError",
    "VoiceProfileNotFoundError",
    "VoiceProfileService",
    "VoiceProfileStorage",
    "VoiceProfileStorageError",
    "VoiceProfileValidationError",
    "VoiceScriptNotFoundError",
]
