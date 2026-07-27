"""Schemas, enums and typed errors for the VOD-pipeline backend core.

This module is the single source of truth for the JSON shapes written to
``twitch_profiles/{profile_id}/profile.json`` and
``vods/{vod_id}/metadata.json``. It deliberately mirrors the conventions
of :mod:`voice_profiles.schemas` but does not import anything from the
voice-profile vertical slice: the VOD-pipeline core must stay isolated.
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

# Hard cap for the first sync so we never pull a channel's entire VOD
# history on a single click. Documented and overridable via the service.
DEFAULT_SYNC_LIMIT = 100


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VodStatus(str, Enum):
    """Lifecycle status of a VOD record."""

    DISCOVERED = "DISCOVERED"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    VERIFYING = "VERIFYING"
    READY = "READY"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


# Transient states: if the worker exits while in one of these, the record
# is automatically marked FAILED by the reaper / startup recovery.
TRANSIENT_VOD_STATUSES = frozenset({
    VodStatus.QUEUED,
    VodStatus.DOWNLOADING,
    VodStatus.VERIFYING,
})

# Statuses from which a download may be (re)started.
STARTABLE_VOD_STATUSES = frozenset({
    VodStatus.DISCOVERED,
    VodStatus.FAILED,
    VodStatus.CANCELED,
})

# Statuses that may be canceled.
CANCELLABLE_VOD_STATUSES = frozenset({
    VodStatus.QUEUED,
    VodStatus.DOWNLOADING,
    VodStatus.VERIFYING,
})

# Terminal statuses (no active worker).
TERMINAL_VOD_STATUSES = frozenset({
    VodStatus.DISCOVERED,
    VodStatus.READY,
    VodStatus.FAILED,
    VodStatus.CANCELED,
})


# Allowed final containers for the downloaded source file. The worker
# picks the best combined format yt-dlp returns; we only ever expose a
# fixed, safe filename to the outside world.
ALLOWED_SOURCE_CONTAINERS = ("mp4", "mkv", "webm", "ts")


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class VodError(Exception):
    """Base class for all VOD-pipeline core errors."""


class VodValidationError(VodError):
    """Hard validation failure (bad URL, bad id, bad state transition)."""


class VodNotFoundError(VodError):
    """A VOD with the given id does not exist."""


class VodConflictError(VodError):
    """A VOD-level conflict (already running, wrong state)."""


class VodStorageError(VodError):
    """A persistence-layer failure (corrupt JSON, unknown schema, IO)."""


class TwitchProfileError(Exception):
    """Base class for all Twitch-profile core errors."""


class TwitchProfileValidationError(TwitchProfileError):
    """Hard validation failure (bad login, bad url)."""


class TwitchProfileNotFoundError(TwitchProfileError):
    """A profile with the given id does not exist."""


class TwitchProfileConflictError(TwitchProfileError):
    """A profile-level conflict (duplicate Twitch user id, VODs attached)."""


class TwitchProfileStorageError(TwitchProfileError):
    """A persistence-layer failure (corrupt JSON, unknown schema, IO)."""


class TwitchClientError(Exception):
    """Base class for yt-dlp listing / metadata errors."""


class TwitchNotFoundError(TwitchClientError):
    """A Twitch resource (channel / video / clip) was not found."""


# ---------------------------------------------------------------------------
# Twitch profile / VOD models (used for validation only; persistence is
# plain JSON dicts to stay forward-compatible with additive schema changes)
# ---------------------------------------------------------------------------

class ProfileSchemaVersion(BaseModel):
    schema_version: int = SCHEMA_VERSION


class TwitchProfile(BaseModel):
    """A persisted Twitch profile."""

    schema_version: int = SCHEMA_VERSION
    id: str
    login: str
    channel_url: str = ""
    display_name: str = ""
    created_at: str
    updated_at: str
    last_synced_at: Optional[str] = None


class VodProgress(BaseModel):
    percent: Optional[float] = None
    downloaded_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    speed_bytes_per_second: Optional[float] = None
    eta_seconds: Optional[float] = None


class VodDownload(BaseModel):
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    container: Optional[str] = None
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None


class VodSchemaVersion(BaseModel):
    schema_version: int = SCHEMA_VERSION


class TwitchVod(BaseModel):
    """A persisted VOD or clip record."""

    schema_version: int = SCHEMA_VERSION
    id: str
    profile_id: str
    twitch_video_id: str  # yt-dlp entry id; stable for dedup
    source_url: str
    title: str = ""
    description: str = ""
    type: str = "archive"  # "archive" (VOD) or "clip"
    language: str = ""
    published_at: Optional[str] = None
    created_at: str
    duration_seconds: Optional[float] = None
    thumbnail_url: str = ""
    view_count: Optional[int] = None
    status: str = VodStatus.DISCOVERED.value
    progress: VodProgress = Field(default_factory=VodProgress)
    download: VodDownload = Field(default_factory=VodDownload)
    error: Optional[str] = None
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def empty_progress() -> dict[str, Any]:
    return {
        "percent": None,
        "downloaded_bytes": None,
        "total_bytes": None,
        "speed_bytes_per_second": None,
        "eta_seconds": None,
    }


def empty_download() -> dict[str, Any]:
    return {
        "started_at": None,
        "completed_at": None,
        "file_name": None,
        "file_size_bytes": None,
        "container": None,
        "duration_seconds": None,
        "width": None,
        "height": None,
        "video_codec": None,
        "audio_codec": None,
    }
