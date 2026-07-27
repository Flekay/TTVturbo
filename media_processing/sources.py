"""Media source resolution.

The single extension point that maps ``(source_type, source_id)`` to a
concrete, verified media file on disk. In this phase only
``source_type = "twitch_vod"`` is supported, but the resolver is the
place future sources (local file, YouTube, live recording, audio upload)
would plug into.

For a Twitch VOD the resolver requires:

* the VOD record to exist (via :class:`vod_pipeline.storage.VodPipelineStorage`);
* the VOD status to be ``READY`` (downloaded + FFprobe-verified);
* the registered source file to actually exist on disk.

Only VODs with a registered and verified file may be used for audio
extraction. This is enforced here, once, so neither the audio-extraction
service nor the transcription service nor the pipeline need to repeat
the check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from vod_pipeline import (
    TwitchProfileNotFoundError,
    VodNotFoundError,
    VodPipelineStorage,
    VodStatus,
)
from vod_pipeline.service import ffprobe_inspect, FFprobeError

from .schemas import (
    MediaSourceError,
    MediaSourceNotFoundError,
    MediaSourceNotReadyError,
)

logger = logging.getLogger("ttvturbo.media_processing.sources")

SUPPORTED_SOURCE_TYPES = frozenset({"twitch_vod"})


@dataclass
class ResolvedMediaSource:
    """A verified media source ready for audio extraction."""

    source_type: str
    source_id: str
    # Absolute path to the registered, verified source video file.
    file_path: Path
    # Relative path of the file inside the VOD directory (e.g. "source.mp4").
    file_name: str
    title: str
    duration_seconds: Optional[float]
    profile_id: Optional[str]
    profile_login: Optional[str]
    # Download status string from the VOD record (always "READY" here).
    download_status: str
    # The VOD directory (vods/{vod_id}) — artifacts live under here.
    vod_dir: Path
    # The raw VOD record dict (additive fields tolerated by callers).
    vod: dict[str, Any]


class MediaSourceResolver:
    """Resolves a ``(source_type, source_id)`` to a verified media file."""

    def __init__(self, vod_storage: VodPipelineStorage) -> None:
        self.vod_storage = vod_storage

    def resolve(self, source_type: str, source_id: str) -> ResolvedMediaSource:
        if source_type not in SUPPORTED_SOURCE_TYPES:
            raise MediaSourceError(
                f"unsupported source_type {source_type!r}; "
                f"supported: {sorted(SUPPORTED_SOURCE_TYPES)}"
            )
        if source_type == "twitch_vod":
            return self._resolve_twitch_vod(source_id)
        raise MediaSourceError(f"unsupported source_type {source_type!r}")

    def _resolve_twitch_vod(self, vod_id: str) -> ResolvedMediaSource:
        if not isinstance(vod_id, str) or not vod_id.strip():
            raise MediaSourceError("vod_id must be a non-empty string")
        # UUID validation happens inside the storage layer; a non-UUID id
        # raises VodStorageError which we map to NotFound for a clean 404.
        try:
            vod = self.vod_storage.load_vod(vod_id)
        except VodNotFoundError as exc:
            raise MediaSourceNotFoundError(str(exc)) from exc
        except Exception as exc:
            # Storage errors (invalid uuid, path traversal) -> NotFound.
            raise MediaSourceNotFoundError(f"vod not found: {vod_id}") from exc

        status = vod.get("status")
        if status != VodStatus.READY.value:
            raise MediaSourceNotReadyError(
                f"VOD {vod_id} is not READY (current: {status}). "
                f"Download and verify it first."
            )

        download = vod.get("download") or {}
        file_name = download.get("file_name")
        if not file_name:
            raise MediaSourceNotReadyError(
                f"VOD {vod_id} is READY but has no registered file_name."
            )

        vod_dir = self.vod_storage._vod_dir(vod_id)  # noqa: SLF001 - same-package-ish, validated
        file_path = vod_dir / file_name
        if not file_path.is_file():
            raise MediaSourceNotReadyError(
                f"VOD {vod_id} source file {file_name} is missing on disk."
            )
        if file_path.stat().st_size <= 0:
            raise MediaSourceNotReadyError(
                f"VOD {vod_id} source file {file_name} is empty."
            )

        # Resolve the profile for display purposes (best-effort).
        profile_id = vod.get("profile_id")
        profile_login: Optional[str] = None
        if profile_id:
            try:
                profile = self.vod_storage.load_profile(profile_id)
                profile_login = profile.get("login") or profile.get("display_name")
            except TwitchProfileNotFoundError:
                pass
            except Exception:  # pragma: no cover - defensive
                pass

        return ResolvedMediaSource(
            source_type="twitch_vod",
            source_id=vod_id,
            file_path=file_path,
            file_name=file_name,
            title=vod.get("title") or vod.get("twitch_video_id") or vod_id,
            duration_seconds=download.get("duration_seconds") or vod.get("duration_seconds"),
            profile_id=profile_id,
            profile_login=profile_login,
            download_status=status,
            vod_dir=vod_dir,
            vod=vod,
        )

    def get_vod_dir(self, vod_id: str) -> Path:
        """Return the VOD directory for ``vod_id`` after UUID validation.

        Used by the transcription service to locate the artifacts tree
        without re-resolving the source (the audio may already exist).
        Raises :class:`MediaSourceNotFoundError` if the VOD does not exist.
        """
        try:
            self.vod_storage.load_vod(vod_id)
        except VodNotFoundError as exc:
            raise MediaSourceNotFoundError(str(exc)) from exc
        except Exception as exc:
            raise MediaSourceNotFoundError(f"vod not found: {vod_id}") from exc
        return self.vod_storage._vod_dir(vod_id)  # noqa: SLF001
