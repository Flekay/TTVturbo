"""VOD-pipeline service: profiles, VODs, sync, import, download orchestration.

Composes :class:`ChannelLister` (yt-dlp-based, no API credentials) and
:class:`VodPipelineStorage` (atomic persistence) and adds:

* Twitch profile lifecycle (create from channel URL, delete with
  VOD-reference protection);
* VOD + clip sync (yt-dlp --flat-playlist, dedup by source URL, no
  auto-download);
* manual VOD/clip link import (twitch.tv/videos/<id> and
  twitch.tv/<channel>/clip/<slug>, dedup);
* download orchestration (yt-dlp subprocess, single concurrency slot,
  real progress, cancel, retry, restart-recovery, FFprobe verification).

A process-local :class:`threading.Lock` guards every read-modify-write
sequence and the single download slot. No global job system, no Redis.
No Twitch API credentials are needed — yt-dlp handles everything.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

from .schemas import (
    ALLOWED_SOURCE_CONTAINERS,
    CANCELLABLE_VOD_STATUSES,
    DEFAULT_SYNC_LIMIT,
    SCHEMA_VERSION,
    STARTABLE_VOD_STATUSES,
    TRANSIENT_VOD_STATUSES,
    TwitchClientError,
    TwitchNotFoundError,
    TwitchProfileConflictError,
    TwitchProfileNotFoundError,
    TwitchProfileStorageError,
    TwitchProfileValidationError,
    VodConflictError,
    VodNotFoundError,
    VodStatus,
    VodStorageError,
    VodValidationError,
    empty_download,
    empty_progress,
)
from .storage import VodPipelineStorage
from .twitch_client import ChannelLister

from ttvturbo.storage_utils import atomic_write_json as _central_atomic_write_json
from ttvturbo.storage_utils import cleanup_stale_atomic_tmp

logger = logging.getLogger("ttvturbo.vod_pipeline.service")

KILL_GRACE_SECONDS = 5.0
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_TIMEOUT_SECONDS = 0.0  # 0 = no artificial timeout for long VODs

# twitch.tv/videos/<numeric-id> — VOD URL.
TWITCH_VOD_URL_RE = re.compile(
    r"^https?://(?:www\.)?twitch\.tv/videos/(?P<id>[0-9]+)/?$",
    re.IGNORECASE,
)
# twitch.tv/<channel>/clip/<slug> — clip URL.
TWITCH_CLIP_URL_RE = re.compile(
    r"^https?://(?:www\.)?twitch\.tv/(?:[^/]+/)?clip/(?P<slug>[A-Za-z0-9_-]+)/?$",
    re.IGNORECASE,
)
# clips.twitch.tv/<slug> — legacy clip URL.
TWITCH_CLIPS_LEGACY_URL_RE = re.compile(
    r"^https?://(?:www\.)?clips\.twitch\.tv/(?P<slug>[A-Za-z0-9_-]+)/?$",
    re.IGNORECASE,
)
# twitch.tv/<login> — channel URL (no /videos, no /clip, no /videos/<id>).
TWITCH_CHANNEL_URL_RE = re.compile(
    r"^https?://(?:www\.)?twitch\.tv/(?P<login>[A-Za-z0-9_]{4,25})/?$",
    re.IGNORECASE,
)
# A Twitch login: 4-25 chars, lowercase letters, digits, underscores.
TWITCH_LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{4,25}$")


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _normalize_login_or_url(raw: str) -> tuple[str, str]:
    """Return ``(login, channel_url)`` from a raw login or channel URL.

    Raises :class:`TwitchProfileValidationError` for anything that is not
    a plain login or a ``twitch.tv/<login>`` channel URL.
    """
    if not isinstance(raw, str):
        raise TwitchProfileValidationError("login must be a string")
    value = raw.strip()
    if not value:
        raise TwitchProfileValidationError("login must not be empty")
    # Plain login.
    if TWITCH_LOGIN_RE.match(value):
        login = value.lower()
        return login, f"https://www.twitch.tv/{login}"
    # Channel URL.
    m = TWITCH_CHANNEL_URL_RE.match(value)
    if m:
        login = m.group("login").lower()
        return login, f"https://www.twitch.tv/{login}"
    # Helpful, distinct errors for the common wrong inputs.
    if "twitch.tv/videos/" in value:
        raise TwitchProfileValidationError(
            "This is a VOD URL. Add it on the VOD Pipeline page instead."
        )
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        if parsed.netloc.lower() not in ("twitch.tv", "www.twitch.tv"):
            raise TwitchProfileValidationError("Only twitch.tv URLs are supported.")
    raise TwitchProfileValidationError(
        "Enter a Twitch login (e.g. casepayt) or a channel URL "
        "(https://www.twitch.tv/casepayt)."
    )


def parse_twitch_video_url(url: str) -> tuple[str, str]:
    """Return ``(video_id, vod_type)`` from a VOD or clip URL.

    Accepts:
    - ``twitch.tv/videos/<id>`` → ``(<id>, "archive")``
    - ``twitch.tv/<channel>/clip/<slug>`` → ``(<slug>, "clip")``
    - ``clips.twitch.tv/<slug>`` → ``(<slug>, "clip")``

    Raises :class:`VodValidationError` for anything else.
    """
    if not isinstance(url, str):
        raise VodValidationError("url must be a string")
    value = url.strip()
    if not value:
        raise VodValidationError("url must not be empty")
    m = TWITCH_VOD_URL_RE.match(value)
    if m:
        return m.group("id"), "archive"
    m = TWITCH_CLIP_URL_RE.match(value)
    if m:
        return m.group("slug"), "clip"
    m = TWITCH_CLIPS_LEGACY_URL_RE.match(value)
    if m:
        return m.group("slug"), "clip"
    if TWITCH_CHANNEL_URL_RE.match(value):
        raise VodValidationError(
            "This is a channel URL, not a VOD URL. "
            "Use the profile page to add the channel."
        )
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        if parsed.netloc.lower() not in ("twitch.tv", "www.twitch.tv", "clips.twitch.tv"):
            raise VodValidationError("Only twitch.tv VOD or clip URLs are supported.")
    raise VodValidationError(
        "Only twitch.tv/videos/<id> or twitch.tv/<channel>/clip/<slug> URLs are supported."
    )


def _profile_from_login(login: str, channel_url: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": _new_uuid(),
        "login": login,
        "channel_url": channel_url,
        "display_name": login,
        "avatar_url": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_synced_at": None,
    }


def _parse_thumbnail_timestamp(thumbnail_url: str) -> Optional[str]:
    """Extract the publish timestamp from a Twitch VOD thumbnail URL.

    Twitch VOD thumbnails contain a Unix timestamp segment, e.g.:
    ``.../casepayt_317894458983_1785083053//thumb/thumb0-320x180.jpg``
    The last ``_<10+ digits>//thumb`` group is the VOD creation time.
    Returns an ISO string or None if the pattern doesn't match.
    """
    if not thumbnail_url:
        return None
    m = re.search(r"_(\d{10,})//thumb", thumbnail_url)
    if not m:
        return None
    try:
        ts = int(m.group(1))
        return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _entry_to_vod(entry: dict, profile_id: str) -> dict:
    """Convert a normalized yt-dlp entry dict into a VOD record."""
    duration = entry.get("duration")
    if duration is not None:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = None
    published_at = None
    ts = entry.get("timestamp")
    if ts is not None:
        try:
            published_at = _dt.datetime.fromtimestamp(
                float(ts), tz=_dt.timezone.utc
            ).isoformat()
        except (TypeError, ValueError, OSError):
            published_at = None
    if not published_at and entry.get("upload_date"):
        try:
            d = str(entry["upload_date"])
            if len(d) == 8:
                published_at = (
                    f"{d[0:4]}-{d[4:6]}-{d[6:8]}T00:00:00+00:00"
                )
        except (TypeError, ValueError):
            pass
    # VODs from yt-dlp --flat-playlist have no timestamp/upload_date.
    # Fall back to the timestamp embedded in the Twitch thumbnail URL.
    if not published_at:
        published_at = _parse_thumbnail_timestamp(
            str(entry.get("thumbnail", "") or "")
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "id": _new_uuid(),
        "profile_id": profile_id,
        "twitch_video_id": str(entry.get("id", "")),
        "source_url": str(entry.get("url", "")),
        "title": str(entry.get("title", "") or ""),
        "description": "",
        "type": str(entry.get("type", "archive") or "archive"),
        "language": str(entry.get("language", "") or ""),
        "published_at": published_at,
        "created_at": _now_iso(),
        "duration_seconds": duration,
        "thumbnail_url": str(entry.get("thumbnail", "") or ""),
        "view_count": entry.get("view_count"),
        "status": VodStatus.DISCOVERED.value,
        "progress": empty_progress(),
        "download": empty_download(),
        "error": None,
        "updated_at": _now_iso(),
    }


class VodPipelineService:
    """High-level service for Twitch profiles and VOD/clip downloads."""

    def __init__(
        self,
        storage: VodPipelineStorage,
        channel_lister: ChannelLister,
        download_dir: Path,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sync_limit: int = DEFAULT_SYNC_LIMIT,
        library_service: Optional[Any] = None,
    ) -> None:
        self.storage = storage
        self.lister = channel_lister
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max(1, int(max_concurrent))
        self.timeout_seconds = float(timeout_seconds)
        self.sync_limit = max(1, int(sync_limit))
        self.library_service = library_service
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen] = {}
        self._active_log_fh: dict[str, Any] = {}
        # Recover persisted state on startup.
        self._recover_on_startup()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _now_iso() -> str:
        return _now_iso()

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        """Atomically write JSON metadata.

        Delegates to the central :func:`storage_utils.atomic_write_json` so
        every atomic write in the codebase uses the same reserved temp
        file pattern (``.{name}.{pid}.{ns}.tmp``) and the same
        Windows-lock retry behaviour.  This avoids the race where a
        cleanup routine deletes a fixed-name ``.tmp`` file between
        ``open()`` and ``os.replace()``.
        """
        _central_atomic_write_json(path, payload, VodStorageError, kind="vod")

    def _write_vod(self, vod_id: str, payload: dict) -> None:
        self.storage.save_vod(payload)

    def _read_vod(self, vod_id: str) -> Optional[dict]:
        try:
            return self.storage.load_vod(vod_id)
        except VodNotFoundError:
            return None

    def _vod_dir(self, vod_id: str) -> Path:
        return self.storage.vod_dir(vod_id)

    def _source_path_for(self, vod: dict) -> Optional[Path]:
        name = (vod.get("download") or {}).get("file_name")
        if not name:
            return None
        p = (self._vod_dir(vod["id"]) / name).resolve()
        try:
            p.relative_to(self.storage.vods_dir.resolve())
        except ValueError:
            return None
        return p if p.is_file() else None

    def _ready_source_exists(self, vod: dict) -> bool:
        """True if a READY VOD's source file is still reachable.

        Downloaded VOD files are moved into the persistent library by
        ``promote_vod_file``; the VOD only keeps a ``library_item_id``
        back-reference. So we must check the library first, and only fall
        back to the VOD dir when the library is not in use.
        """
        library_item_id = vod.get("library_item_id")
        if library_item_id and self.library_service is not None:
            try:
                path = self.library_service.item_file_path(library_item_id)
            except Exception:
                return False
            return path.is_file()
        return self._source_path_for(vod) is not None

    def _library_item_file_exists(self, library_item_id: str) -> bool:
        """True if the library item exists and its source file is on disk."""
        if self.library_service is None:
            return False
        try:
            return self.library_service.item_file_path(library_item_id).is_file()
        except Exception:
            return False

    # ------------------------------------------------------------------ startup
    def _recover_on_startup(self) -> None:
        """Mark any transient-state VOD as FAILED after a restart.

        Also removes leftover ``.part`` / temp download files so a
        seemingly-finished file never appears as READY without
        verification. Faulty metadata never prevents the server start.
        """
        for vod in list(self.storage.iter_vods()):
            status_str = vod.get("status")
            try:
                status = VodStatus(status_str)
            except ValueError:
                logger.warning("Skipping vod with unknown status %s", status_str)
                continue
            if status in TRANSIENT_VOD_STATUSES:
                vod["status"] = VodStatus.FAILED.value
                vod["error"] = (
                    vod.get("error")
                    or "Download worker was interrupted by a server restart."
                )
                vod["progress"] = empty_progress()
                if not (vod.get("download") or {}).get("completed_at"):
                    vod.setdefault("download", {})["completed_at"] = None
                vod["updated_at"] = self._now_iso()
                self._cleanup_vod_partials(vod["id"])
                # Reap stale atomic-transaction temp files (left by a
                # writer that died before os.replace).  Active transactions
                # are never touched because they are younger than the age
                # threshold.
                cleanup_stale_atomic_tmp(self._vod_dir(vod["id"]))
                try:
                    self._write_vod(vod["id"], vod)
                except OSError as exc:  # pragma: no cover - defensive
                    logger.warning("Could not persist recovery for %s: %s", vod["id"], exc)
            elif status == VodStatus.READY:
                # A READY record must keep a valid source file; if it is
                # gone, downgrade to FAILED so the user can retry. The file
                # normally lives in the library (moved there by
                # ``promote_vod_file``); only fall back to the VOD dir when
                # no library_item_id is set (e.g. library not configured).
                if not self._ready_source_exists(vod):
                    vod["status"] = VodStatus.FAILED.value
                    vod["error"] = "Source file is missing for a READY VOD."
                    vod["updated_at"] = self._now_iso()
                    try:
                        self._write_vod(vod["id"], vod)
                    except OSError:  # pragma: no cover
                        pass

    def _cleanup_vod_partials(self, vod_id: str) -> None:
        """Remove leftover download/worker partials for a VOD.

        Only removes files that are unambiguously download artifacts:
        yt-dlp fragment files (``.dl_*``) and binary download parts
        (``*.part``).  Atomic-JSON transaction temp files
        (``.{name}.{pid}.{ns}.tmp``) are NEVER touched here — deleting one
        between ``open()`` and ``os.replace()`` would break an active
        metadata write (cancel/retry/recovery).  Stale atomic temp files
        are reaped separately by :func:`cleanup_stale_atomic_tmp` based on
        age, not by this routine.
        """
        vod_dir = self._vod_dir(vod_id)
        if not vod_dir.is_dir():
            return
        for entry in vod_dir.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if name.startswith(".dl_") or name.endswith(".part"):
                try:
                    entry.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------ profiles
    def list_profiles(self) -> list[dict]:
        profiles = list(self.storage.iter_profiles())
        profiles.sort(key=lambda p: p.get("display_name", "").lower())
        return profiles

    def get_profile(self, profile_id: str) -> dict:
        return self.storage.load_profile(profile_id)

    def create_profile(self, login_or_url: str) -> dict:
        login, channel_url = _normalize_login_or_url(login_or_url)
        # Dedup by login — one profile per channel.
        for existing in self.storage.iter_profiles():
            if existing.get("login", "").lower() == login:
                raise TwitchProfileConflictError(
                    f"A profile for {existing.get('login')} already exists."
                )
        profile = _profile_from_login(login, channel_url)
        # Best-effort: fetch channel info (display name, avatar) via yt-dlp
        # so the profile is populated immediately on creation.
        if login:
            try:
                info = self.lister.get_channel_info(login)
                if info.get("display_name"):
                    profile["display_name"] = info["display_name"]
                if info.get("avatar_url"):
                    profile["avatar_url"] = info["avatar_url"]
            except Exception:
                # Channel info is best-effort; don't fail the create.
                pass
        self.storage.save_profile(profile)
        return profile

    def refresh_profile(self, profile_id: str) -> dict:
        profile = self.storage.load_profile(profile_id)
        login = str(profile.get("login", ""))
        # Best-effort: fetch channel info (display name, avatar) via yt-dlp.
        if login:
            try:
                info = self.lister.get_channel_info(login)
                if info.get("display_name"):
                    profile["display_name"] = info["display_name"]
                if info.get("avatar_url"):
                    profile["avatar_url"] = info["avatar_url"]
            except Exception:
                # Channel info is best-effort; don't fail the refresh.
                pass
        profile["updated_at"] = self._now_iso()
        self.storage.save_profile(profile)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        # Validate id + existence first (raises NotFound / Storage).
        self.storage.load_profile(profile_id)
        # VODs are session data tied to the profile for sync. Delete VOD
        # metadata + temp files, but the downloaded video files live in
        # the library and survive — we just unlink the back-reference.
        for vod in self.storage.find_vods_for_profile(profile_id):
            if vod.get("library_item_id") and self.library_service:
                self.library_service.unlink_vod(vod["id"])
            self.storage.delete_vod(vod["id"])
        return self.storage.delete_profile(profile_id)

    def profile_vod_count(self, profile_id: str) -> int:
        return len(self.storage.find_vods_for_profile(profile_id))

    # ------------------------------------------------------------------ sync
    def sync_vods(self, profile_id: str, limit: Optional[int] = None) -> dict:
        profile = self.storage.load_profile(profile_id)
        login = str(profile.get("login", ""))
        if not login:
            raise TwitchProfileValidationError("Profile has no login set.")
        cap = int(limit) if limit and limit > 0 else self.sync_limit
        try:
            vod_entries = self.lister.list_vods(login, limit=cap)
            clip_entries = self.lister.list_clips(login, limit=cap)
        except TwitchNotFoundError as exc:
            raise TwitchProfileValidationError(
                f"Twitch channel not found: {login}"
            ) from exc
        # Best-effort: update channel info (display name, avatar) during sync.
        try:
            info = self.lister.get_channel_info(login)
            if info.get("display_name"):
                profile["display_name"] = info["display_name"]
            if info.get("avatar_url"):
                profile["avatar_url"] = info["avatar_url"]
        except Exception:
            pass
        except TwitchClientError as exc:
            raise TwitchClientError(f"Could not list channel VODs: {exc}") from exc
        created = 0
        updated = 0
        unchanged = 0
        for entry in (*vod_entries, *clip_entries):
            source_url = str(entry.get("url", ""))
            if not source_url:
                continue
            video_id = str(entry.get("id", ""))
            existing = self.storage.find_vod_by_twitch_video_id(video_id)
            if existing is None:
                vod = _entry_to_vod(entry, profile_id)
                # Auto-link: if this VOD's twitch_video_id is already in
                # the library (downloaded before, possibly under a deleted
                # profile), mark it READY immediately and link the library
                # item back to this new VOD record. This restores the
                # connection automatically on re-sync without requiring
                # the user to click download again.
                if self.library_service is not None and video_id:
                    lib_item = self.library_service.find_by_twitch_video_id(video_id)
                    if lib_item is not None:
                        vod["status"] = VodStatus.READY.value
                        vod["library_item_id"] = lib_item["id"]
                        download = dict(vod.get("download") or empty_download())
                        download["completed_at"] = lib_item.get("updated_at") or self._now_iso()
                        download["container"] = lib_item.get("container") or "mp4"
                        download["duration_seconds"] = lib_item.get("duration_seconds")
                        download["file_size_bytes"] = lib_item.get("file_size_bytes")
                        vod["download"] = download
                        # Link the library item back to this new VOD.
                        try:
                            self.library_service.link_vod(lib_item["id"], vod["id"])
                        except Exception:
                            pass
                self.storage.save_vod(vod)
                created += 1
                continue
            # Update mutable metadata but never touch download state.
            new_meta = _entry_to_vod(entry, profile_id)
            new_meta["id"] = existing["id"]
            new_meta["created_at"] = existing.get("created_at", new_meta["created_at"])
            new_meta["status"] = existing.get("status", VodStatus.DISCOVERED.value)
            new_meta["progress"] = existing.get("progress") or empty_progress()
            new_meta["download"] = existing.get("download") or empty_download()
            new_meta["error"] = existing.get("error")
            changed_fields = ("title", "thumbnail_url", "view_count", "duration_seconds", "published_at")
            if any(existing.get(f) != new_meta.get(f) for f in changed_fields):
                new_meta["updated_at"] = self._now_iso()
                self.storage.save_vod(new_meta)
                updated += 1
            else:
                unchanged += 1
        profile["last_synced_at"] = self._now_iso()
        profile["updated_at"] = self._now_iso()
        self.storage.save_profile(profile)
        return {
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "total": created + updated + unchanged,
        }

    # ------------------------------------------------------------------ import
    def import_vod(self, url: str, profile_id: Optional[str] = None) -> dict:
        if profile_id:
            self.storage.load_profile(profile_id)
        video_id, vod_type = parse_twitch_video_url(url)
        # Dedup by Twitch video id across all profiles first.
        existing = self.storage.find_vod_by_twitch_video_id(video_id)
        if existing is not None:
            if existing.get("profile_id") == profile_id:
                return existing
            if existing.get("profile_id") is None and profile_id:
                # Detached VOD (profile was deleted) — re-attach to this profile.
                existing["profile_id"] = profile_id
                existing["updated_at"] = self._now_iso()
                self.storage.save_vod(existing)
                return existing
            if profile_id is None:
                return existing
            raise VodConflictError(
                "This VOD is already imported under a different profile."
            )
        # Fetch metadata via yt-dlp (no API credentials needed).
        try:
            info = self.lister.get_video_info(url)
        except TwitchNotFoundError as exc:
            raise VodValidationError(f"Twitch video not found: {video_id}") from exc
        except TwitchClientError as exc:
            raise TwitchClientError(f"Could not fetch video metadata: {exc}") from exc
        vod = _entry_to_vod(info, profile_id)
        self.storage.save_vod(vod)
        return vod

    # ------------------------------------------------------------------ vods
    def list_vods(
        self,
        profile_id: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort: str = "newest",
    ) -> list[dict]:
        items = list(self.storage.iter_vods())
        if profile_id:
            items = [v for v in items if v.get("profile_id") == profile_id]
        if status:
            items = [v for v in items if v.get("status") == status]
        if search:
            needle = search.strip().lower()
            if needle:
                items = [
                    v for v in items
                    if needle in (v.get("title", "") or "").lower()
                    or needle in (v.get("twitch_video_id", "") or "").lower()
                ]
        # Sorting.
        def _key_newest(v: dict) -> Any:
            return v.get("published_at") or v.get("created_at") or ""

        def _key_oldest(v: dict) -> Any:
            return _key_newest(v)

        def _key_longest(v: dict) -> Any:
            return -(v.get("duration_seconds") or 0)

        def _key_shortest(v: dict) -> Any:
            return (v.get("duration_seconds") or 0)

        sort_map = {
            "newest": ("desc", _key_newest),
            "oldest": ("asc", _key_oldest),
            "longest": ("desc", _key_longest),
            "shortest": ("asc", _key_shortest),
        }
        direction, key = sort_map.get(sort, sort_map["newest"])
        items.sort(key=key, reverse=(direction == "desc"))
        return items

    def get_vod(self, vod_id: str) -> dict:
        return self.storage.load_vod(vod_id)

    # ------------------------------------------------------------------ downloads
    def _slot_available(self) -> bool:
        # Reap any dead processes first.
        dead = [
            vid for vid, proc in self._active.items()
            if proc.poll() is not None
        ]
        for vid in dead:
            self._close_log(vid)
            self._active.pop(vid, None)
        return len(self._active) < self.max_concurrent

    def _close_log(self, vod_id: str) -> None:
        fh = self._active_log_fh.pop(vod_id, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def start_download(self, vod_id: str) -> dict:
        vod = self.storage.load_vod(vod_id)
        status_str = vod.get("status")
        try:
            status = VodStatus(status_str)
        except ValueError as exc:
            raise VodValidationError(f"unknown vod status {status_str!r}") from exc
        # Duplication check: if this VOD's twitch_video_id is already in
        # the library (downloaded before, possibly under a deleted profile),
        # skip the download and link the existing library item instead.
        if self.library_service is not None and vod.get("twitch_video_id"):
            existing = self.library_service.find_by_twitch_video_id(vod["twitch_video_id"])
            if existing is not None:
                vod["library_item_id"] = existing["id"]
                vod["status"] = VodStatus.READY.value
                vod["error"] = None
                vod["progress"] = empty_progress()
                download = dict(vod.get("download") or empty_download())
                download["completed_at"] = self._now_iso()
                download["container"] = existing.get("container") or "mp4"
                download["duration_seconds"] = existing.get("duration_seconds")
                download["file_size_bytes"] = existing.get("file_size_bytes")
                vod["download"] = download
                vod["updated_at"] = self._now_iso()
                self._write_vod(vod_id, vod)
                # Link the library item back to this VOD.
                try:
                    self.library_service.link_vod(existing["id"], vod_id)
                except Exception:
                    pass
                return self.storage.load_vod(vod_id)
        # Auto-recover orphaned workers: the VOD is in a transient state
        # (DOWNLOADING/QUEUED/VERIFYING) but this service instance has no
        # active worker for it. This happens after a server restart where
        # the old worker subprocess became an orphan. Mark it FAILED so
        # the user can retry instead of getting a permanent 409 lockout.
        if status in TRANSIENT_VOD_STATUSES:
            with self._lock:
                proc = self._active.get(vod_id)
            if proc is None or proc.poll() is not None:
                # No live worker — orphaned. Recover and proceed.
                self._mark_failed(
                    vod_id,
                    "Download worker was interrupted by a server restart.",
                )
                self._cleanup_vod_partials(vod_id)
                vod = self.storage.load_vod(vod_id)
                status = VodStatus(vod.get("status"))
            else:
                raise VodConflictError(
                    f"Download is already running (current: {status.value}). "
                    f"Cancel it first if you want to restart."
                )
        if status not in STARTABLE_VOD_STATUSES:
            raise VodConflictError(
                f"Download can only start from DISCOVERED, FAILED or CANCELED "
                f"(current: {status.value})."
            )
        with self._lock:
            if not self._slot_available():
                raise VodConflictError(
                    "A VOD download is already running. Wait for it to finish "
                    "or cancel it first."
                )
            # Atomically reserve the slot + transition to QUEUED.
            vod["status"] = VodStatus.QUEUED.value
            vod["error"] = None
            vod["progress"] = empty_progress()
            download = dict(vod.get("download") or empty_download())
            download["started_at"] = self._now_iso()
            download["completed_at"] = None
            download["file_name"] = None
            download["file_size_bytes"] = None
            download["container"] = None
            download["duration_seconds"] = None
            download["width"] = None
            download["height"] = None
            download["video_codec"] = None
            download["audio_codec"] = None
            vod["download"] = download
            vod["updated_at"] = self._now_iso()
            self._write_vod(vod_id, vod)
        # Build the job file and spawn the worker outside the lock.
        self._cleanup_vod_partials(vod_id)
        self._spawn_worker(vod_id, vod)
        return self.storage.load_vod(vod_id)

    def _spawn_worker(self, vod_id: str, vod: dict) -> None:
        vod_dir = self._vod_dir(vod_id)
        vod_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = self.storage._vod_path(vod_id)  # noqa: SLF001
        job = {
            "vod_id": vod_id,
            "source_url": vod.get("source_url", ""),
            "output_dir": str(vod_dir),
            "metadata_path": str(metadata_path),
        }
        job_path = vod_dir / "job.json"
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh, indent=2, ensure_ascii=False)
        log_path = self.storage.vod_worker_log_path(vod_id)
        try:
            log_fh = open(log_path, "wb", buffering=0)
        except OSError as exc:
            self._mark_failed(vod_id, f"Could not open worker log file: {exc}")
            raise VodConflictError(f"Could not open worker log file: {exc}") from exc
        cmd = [sys.executable, "-m", "ttvturbo.vod_pipeline.downloader_worker", str(job_path)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            try:
                log_fh.close()
            except OSError:
                pass
            self._mark_failed(vod_id, f"Could not start worker subprocess: {exc}")
            raise VodConflictError(f"Could not start worker subprocess: {exc}") from exc
        with self._lock:
            self._active[vod_id] = proc
            self._active_log_fh[vod_id] = log_fh
        reaper = threading.Thread(
            target=self._reap_worker,
            args=(vod_id, proc, log_fh),
            daemon=True,
            name=f"vod-reaper-{vod_id}",
        )
        reaper.start()

    def _reap_worker(self, vod_id: str, proc: subprocess.Popen, log_fh: Any) -> None:
        timed_out = False
        try:
            if self.timeout_seconds and self.timeout_seconds > 0:
                exit_code = proc.wait(timeout=self.timeout_seconds)
            else:
                exit_code = proc.wait()
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate(proc)
            try:
                exit_code = proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                try:
                    exit_code = proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    exit_code = -1
        except Exception:  # pragma: no cover - defensive
            exit_code = -1
        try:
            log_fh.close()
        except OSError:
            pass
        self._finalize_after_exit(vod_id, exit_code, timed_out)
        with self._lock:
            if self._active.get(vod_id) is proc:
                self._active.pop(vod_id, None)
                self._active_log_fh.pop(vod_id, None)

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass

    # ------------------------------------------------------------------ shutdown
    def shutdown(self) -> None:
        """Terminate all active download workers and close log handles.

        Idempotent: safe to call multiple times.  Each worker receives a
        graceful ``terminate()`` then a hard ``kill()`` after the grace
        period.  Log file handles are closed.  Does not raise — a shutdown
        failure in one worker does not block the rest.
        """
        from ttvturbo.lifecycle import terminate_subprocess

        with self._lock:
            items = list(self._active.items())
        for vod_id, proc in items:
            terminate_subprocess(proc, label=f"vod-worker-{vod_id}")
        with self._lock:
            for vod_id in list(self._active.keys()):
                self._active.pop(vod_id, None)
            for vod_id in list(self._active_log_fh.keys()):
                fh = self._active_log_fh.pop(vod_id, None)
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass

    def _finalize_after_exit(self, vod_id: str, exit_code: int, timed_out: bool) -> None:
        vod = self._read_vod(vod_id)
        if vod is None:
            return
        status_str = vod.get("status")
        try:
            status = VodStatus(status_str)
        except ValueError:
            self._mark_failed(vod_id, f"Worker exited with code {exit_code} and left an unknown status.")
            return
        if status == VodStatus.VERIFYING:
            # Worker finished the download; run FFprobe verification.
            self._verify_with_ffprobe(vod_id)
            return
        if status in TRANSIENT_VOD_STATUSES:
            if timed_out:
                reason = f"Download timed out after {self.timeout_seconds:.0f}s and was terminated."
            else:
                reason = f"Download worker exited with code {exit_code} before completion."
            self._mark_failed(vod_id, reason)
            self._cleanup_vod_partials(vod_id)
            return
        # READY / FAILED / CANCELED / DISCOVERED: leave as-is, but clean
        # any leftover partials.
        self._cleanup_vod_partials(vod_id)

    # ------------------------------------------------------------------ ffprobe
    def _verify_with_ffprobe(self, vod_id: str) -> None:
        vod = self._read_vod(vod_id)
        if vod is None:
            return
        vod["status"] = VodStatus.VERIFYING.value
        vod["updated_at"] = self._now_iso()
        self._write_vod(vod_id, vod)
        src = self._source_path_for(vod)
        if src is None:
            self._mark_failed(vod_id, "Downloaded file is missing before verification.")
            return
        try:
            info = ffprobe_inspect(src)
        except FFprobeError as exc:
            self._mark_failed(vod_id, f"FFprobe verification failed: {exc}")
            return
        except FileNotFoundError:
            self._mark_failed(vod_id, "ffprobe not found on server PATH.")
            return
        download = dict(vod.get("download") or empty_download())
        download.update({
            "file_name": src.name,
            "file_size_bytes": src.stat().st_size,
            "container": info["container"],
            "duration_seconds": info["duration_seconds"],
            "width": info["width"],
            "height": info["height"],
            "video_codec": info["video_codec"],
            "audio_codec": info["audio_codec"],
            "completed_at": self._now_iso(),
        })
        # Promote the downloaded file into the persistent library. The
        # file is moved from the VOD temp dir to the library; the VOD
        # keeps a library_item_id back-reference for file serving.
        library_item_id = vod.get("library_item_id")
        # If a library_item_id is set but the referenced item (or its file)
        # is gone, the back-reference is dangling — treat it as unset so the
        # freshly downloaded file gets re-promoted instead of being deleted.
        if (
            self.library_service is not None
            and library_item_id
            and not self._library_item_file_exists(library_item_id)
        ):
            logger.warning(
                "VOD %s has dangling library_item_id %s; re-promoting file.",
                vod_id, library_item_id,
            )
            library_item_id = None
            vod["library_item_id"] = None
        if self.library_service is not None and not library_item_id:
            try:
                item = self.library_service.promote_vod_file(
                    vod_id=vod_id,
                    twitch_video_id=vod.get("twitch_video_id", ""),
                    title=vod.get("title") or vod.get("twitch_video_id") or vod_id,
                    source_file=src,
                    container=info["container"],
                    duration_seconds=info["duration_seconds"],
                    file_size_bytes=src.stat().st_size,
                )
                library_item_id = item["id"]
            except Exception as exc:
                self._mark_failed(vod_id, f"Library promotion failed: {exc}")
                return
        elif self.library_service is not None and library_item_id:
            # File already in library (e.g. re-download after profile
            # deletion). Just update the vod_id back-reference.
            try:
                self.library_service.link_vod(library_item_id, vod_id)
            except Exception:
                pass  # best-effort
        vod["status"] = VodStatus.READY.value
        vod["error"] = None
        vod["progress"] = empty_progress()
        vod["download"] = download
        vod["library_item_id"] = library_item_id
        vod["updated_at"] = self._now_iso()
        self._write_vod(vod_id, vod)
        # Clean up the temp file from the VOD dir — it's now in the library.
        if library_item_id and src.is_file():
            try:
                src.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ cancel/retry
    def cancel_download(self, vod_id: str) -> dict:
        vod = self.storage.load_vod(vod_id)
        status_str = vod.get("status")
        try:
            status = VodStatus(status_str)
        except ValueError as exc:
            raise VodValidationError(f"unknown vod status {status_str!r}") from exc
        if status not in CANCELLABLE_VOD_STATUSES:
            raise VodConflictError(
                f"Download can only be canceled while QUEUED, DOWNLOADING or "
                f"VERIFYING (current: {status.value})."
            )
        with self._lock:
            proc = self._active.get(vod_id)
        if proc is not None and proc.poll() is None:
            self._terminate(proc)
            try:
                proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                try:
                    proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    pass
            with self._lock:
                if self._active.get(vod_id) is proc:
                    self._active.pop(vod_id, None)
            self._close_log(vod_id)
        vod = self.storage.load_vod(vod_id)
        vod["status"] = VodStatus.CANCELED.value
        vod["error"] = "Download was canceled by the user."
        vod["progress"] = empty_progress()
        download = dict(vod.get("download") or empty_download())
        download["completed_at"] = None
        vod["download"] = download
        vod["updated_at"] = self._now_iso()
        self._write_vod(vod_id, vod)
        self._cleanup_vod_partials(vod_id)
        # Remove a finalized source file too: a CANCELED VOD must not
        # expose a seemingly-finished file.
        src = self._source_path_for(vod)
        if src is not None:
            try:
                src.unlink()
            except OSError:
                pass
        return self.storage.load_vod(vod_id)

    def retry_download(self, vod_id: str) -> dict:
        vod = self.storage.load_vod(vod_id)
        status_str = vod.get("status")
        try:
            status = VodStatus(status_str)
        except ValueError as exc:
            raise VodValidationError(f"unknown vod status {status_str!r}") from exc
        if status not in (VodStatus.FAILED, VodStatus.CANCELED):
            raise VodConflictError(
                "Retry is only allowed for FAILED or CANCELED downloads."
            )
        return self.start_download(vod_id)

    # ------------------------------------------------------------------ delete
    def delete_vod(self, vod_id: str) -> bool:
        vod = self.storage.load_vod(vod_id)
        status_str = vod.get("status")
        try:
            status = VodStatus(status_str)
        except ValueError:
            status = None
        if status in CANCELLABLE_VOD_STATUSES:
            # Abort the worker first.
            with self._lock:
                proc = self._active.get(vod_id)
            if proc is not None and proc.poll() is None:
                self._terminate(proc)
                try:
                    proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    self._kill(proc)
                with self._lock:
                    if self._active.get(vod_id) is proc:
                        self._active.pop(vod_id, None)
                self._close_log(vod_id)
        # Unlink the library back-reference (the library item survives).
        if vod.get("library_item_id") and self.library_service:
            try:
                self.library_service.unlink_vod(vod_id)
            except Exception:
                pass
        return self.storage.delete_vod(vod_id)

    # ------------------------------------------------------------------ file
    def ready_file_path(self, vod_id: str) -> Optional[Path]:
        vod = self.storage.load_vod(vod_id)
        if vod.get("status") != VodStatus.READY.value:
            return None
        # Prefer the library file (the canonical home for downloaded videos).
        library_item_id = vod.get("library_item_id")
        if library_item_id and self.library_service:
            try:
                return self.library_service.item_file_path(library_item_id)
            except Exception:
                pass
        # Fallback: file still in the VOD dir (e.g. library not configured
        # or migration in progress).
        return self._source_path_for(vod)

    # ------------------------------------------------------------------ misc
    def _mark_failed(self, vod_id: str, reason: str) -> None:
        vod = self._read_vod(vod_id)
        if vod is None:
            return
        vod["status"] = VodStatus.FAILED.value
        vod["error"] = reason
        vod["progress"] = empty_progress()
        download = dict(vod.get("download") or empty_download())
        download["completed_at"] = None
        vod["download"] = download
        vod["updated_at"] = self._now_iso()
        try:
            self._write_vod(vod_id, vod)
        except OSError:  # pragma: no cover
            pass

    def worker_log_excerpt(self, vod_id: str, max_bytes: int = 8192) -> Optional[str]:
        """Return a bounded, sanitized tail of the worker log."""
        path = self.storage.vod_worker_log_path(vod_id)
        if not path.is_file():
            return None
        try:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                if size > max_bytes:
                    fh.seek(-max_bytes, os.SEEK_END)
                data = fh.read()
        except OSError:
            return None
        text = data.decode("utf-8", errors="replace")
        # Scrub absolute paths so the log excerpt never leaks them.
        text = re.sub(r"[A-Za-z]:\\[^\s\"']+|/[A-Za-z0-9_./-]+", "[path]", text)
        return text[-max_bytes:]

    # ------------------------------------------------------------------ status
    def active_download_count(self) -> int:
        with self._lock:
            self._slot_available()  # reap dead
            return len(self._active)

    def aggregate_status(self) -> dict:
        vods = list(self.storage.iter_vods())
        ready = sum(1 for v in vods if v.get("status") == VodStatus.READY.value)
        failed = sum(1 for v in vods if v.get("status") == VodStatus.FAILED.value)
        active = sum(1 for v in vods if VodStatus(v.get("status", "")) in TRANSIENT_VOD_STATUSES) if vods else 0
        downloaded_bytes = 0
        for v in vods:
            if v.get("status") == VodStatus.READY.value:
                size = (v.get("download") or {}).get("file_size_bytes") or 0
                downloaded_bytes += int(size)
        return {
            "profiles": len(list(self.storage.iter_profiles())),
            "vods": len(vods),
            "ready": ready,
            "active": active,
            "failed": failed,
            "downloaded_bytes": downloaded_bytes,
        }


# ---------------------------------------------------------------------------
# FFprobe verification
# ---------------------------------------------------------------------------

class FFprobeError(Exception):
    """Raised when ffprobe cannot verify the file as a valid video."""


def ffprobe_inspect(path: Path) -> dict:
    """Run ffprobe and verify the file is a real, playable video.

    Returns a dict with container, duration, width, height, video_codec,
    audio_codec. Raises :class:`FFprobeError` if the file is missing,
    empty, unreadable, or lacks a video or audio stream.
    """
    if not path.is_file():
        raise FFprobeError("file does not exist")
    if path.stat().st_size <= 0:
        raise FFprobeError("file is empty")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise FileNotFoundError("ffprobe not found on PATH")
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise FFprobeError(f"ffprobe failed: {stderr}")
    try:
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise FFprobeError(f"ffprobe returned non-JSON output: {exc}") from exc
    streams = payload.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise FFprobeError("no video stream found")
    if audio_stream is None:
        raise FFprobeError("no audio stream found")
    fmt = payload.get("format") or {}
    duration_seconds = None
    try:
        duration_seconds = float(fmt.get("duration") or video_stream.get("duration") or 0)
    except (TypeError, ValueError):
        duration_seconds = None
    if duration_seconds is not None and duration_seconds <= 0:
        raise FFprobeError("duration is not plausible")
    width = video_stream.get("width")
    height = video_stream.get("height")
    if not width or not height:
        raise FFprobeError("width/height missing")
    container = (fmt.get("format_name") or "").split(",")[0] or (path.suffix.lstrip(".") or "mp4")
    return {
        "container": container,
        "duration_seconds": duration_seconds,
        "width": int(width),
        "height": int(height),
        "video_codec": str(video_stream.get("codec_name") or ""),
        "audio_codec": str(audio_stream.get("codec_name") or ""),
    }
