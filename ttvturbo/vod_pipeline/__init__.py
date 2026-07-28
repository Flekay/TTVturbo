"""TTVturbo VOD pipeline backend core.

Isolated module that manages Twitch profiles, VOD/clip metadata, atomic
file-based persistence, the yt-dlp-based channel lister, and the yt-dlp
download worker subprocess. No FastAPI, no React imports. No Twitch API
credentials needed — yt-dlp handles everything.

The FastAPI integration lives in :mod:`vod_pipeline_api` at the repo
root, mirroring the :mod:`voice_profiles` / :mod:`voice_profiles_api`
split.
"""

from __future__ import annotations

from .schemas import (
    DEFAULT_SYNC_LIMIT,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ProfileSchemaVersion,
    VodDownload,
    VodError,
    VodNotFoundError,
    VodProgress,
    VodSchemaVersion,
    VodStatus,
    VodStorageError,
    VodValidationError,
    VodConflictError,
    TwitchProfile,
    TwitchVod,
    TwitchProfileError,
    TwitchProfileNotFoundError,
    TwitchProfileValidationError,
    TwitchProfileConflictError,
    TwitchProfileStorageError,
    TwitchClientError,
    TwitchNotFoundError,
)
from .storage import VodPipelineStorage
from .twitch_client import ChannelLister
from .service import VodPipelineService, ffprobe_inspect, FFprobeError

__all__ = [
    "DEFAULT_SYNC_LIMIT",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ProfileSchemaVersion",
    "TwitchProfile",
    "TwitchProfileError",
    "TwitchProfileNotFoundError",
    "TwitchProfileValidationError",
    "TwitchProfileConflictError",
    "TwitchProfileStorageError",
    "TwitchClientError",
    "TwitchNotFoundError",
    "TwitchVod",
    "VodConflictError",
    "VodDownload",
    "VodError",
    "VodNotFoundError",
    "VodProgress",
    "VodSchemaVersion",
    "VodStatus",
    "VodStorageError",
    "VodValidationError",
    "VodPipelineStorage",
    "VodPipelineService",
    "ChannelLister",
    "ffprobe_inspect",
    "FFprobeError",
]
