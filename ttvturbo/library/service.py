"""Library service — the business logic layer for the persistent video store.

Responsibilities:
* Create library items (from VOD downloads or manual uploads).
* List / get / delete items.
* Find items by ``twitch_video_id`` (duplication check).
* Promote a downloaded VOD file into the library (move from VOD dir).
* Link / unlink VOD back-references.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from .schemas import (
    SCHEMA_VERSION,
    LIFECYCLE_PERSISTENT,
    LIFECYCLE_TEMPORARY,
    SUPPORTED_LIFECYCLES,
    SUPPORTED_FILE_TYPES,
    FILE_TYPE_VIDEO,
    file_type_for_extension,
    file_type_for_filename,
    LibraryConflictError,
    LibraryNotFoundError,
    LibraryStorageError,
    LibraryValidationError,
)
from .storage import LibraryStorage, _now_iso, sanitize_container, SUPPORTED_CONTAINERS

logger = logging.getLogger("ttvturbo.library.service")


class LibraryService:
    """Business logic for the persistent library."""

    def __init__(self, storage: LibraryStorage, *, temporary_ttl_hours: float = 24.0) -> None:
        self.storage = storage
        self.temporary_ttl_hours = max(0.1, float(temporary_ttl_hours))

    def _temporary_expiry(self) -> str:
        expires = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=self.temporary_ttl_hours)
        return expires.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalise_lifecycle(value: Optional[str]) -> str:
        lifecycle = str(value or LIFECYCLE_PERSISTENT).upper()
        if lifecycle not in SUPPORTED_LIFECYCLES:
            raise LibraryValidationError(
                f"lifecycle must be one of {sorted(SUPPORTED_LIFECYCLES)}, got {value!r}"
            )
        return lifecycle

    # ------------------------------------------------------------------ create
    def create_item(
        self,
        source: str,
        title: str,
        file_name: str,
        container: str = "mp4",
        duration_seconds: Optional[float] = None,
        file_size_bytes: Optional[int] = None,
        twitch_video_id: Optional[str] = None,
        vod_id: Optional[str] = None,
        lifecycle: str = LIFECYCLE_PERSISTENT,
        expires_at: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> dict:
        """Create a new library item record (metadata only).

        The caller is responsible for placing the source file into the
        item directory (typically via ``storage.source_file_path(id, container)``).

        ``file_type`` (``video`` | ``audio`` | ``image``) is derived from
        the ``container``/``file_name`` extension when not supplied. It is
        stored on the item so the API and UI can sort and filter by media
        type.
        """
        if source not in ("vod", "upload"):
            raise LibraryValidationError(
                f"source must be 'vod' or 'upload', got {source!r}"
            )
        # Duplication check for VOD downloads.
        if twitch_video_id:
            existing = self.find_by_twitch_video_id(twitch_video_id)
            if existing is not None:
                raise LibraryConflictError(
                    f"A library item for twitch_video_id {twitch_video_id} already exists."
                )
        lifecycle = self._normalise_lifecycle(lifecycle)
        if lifecycle == LIFECYCLE_TEMPORARY and not expires_at:
            expires_at = self._temporary_expiry()
        if lifecycle == LIFECYCLE_PERSISTENT:
            expires_at = None
        # Resolve file_type: explicit arg > filename extension > container.
        resolved_file_type = (
            file_type
            or file_type_for_filename(file_name)
            or file_type_for_extension(container)
            or FILE_TYPE_VIDEO
        )
        if resolved_file_type not in SUPPORTED_FILE_TYPES:
            raise LibraryValidationError(
                f"file_type must be one of {sorted(SUPPORTED_FILE_TYPES)}, got {resolved_file_type!r}"
            )
        item_id = str(uuid.uuid4())
        now = _now_iso()
        meta = {
            "schema_version": SCHEMA_VERSION,
            "id": item_id,
            "source": source,
            "title": title,
            "file_name": file_name,
            "file_size_bytes": file_size_bytes,
            "duration_seconds": duration_seconds,
            "file_type": resolved_file_type,
            "container": container,
            "twitch_video_id": twitch_video_id,
            "vod_id": vod_id,
            "lifecycle": lifecycle,
            "expires_at": expires_at,
            "created_at": now,
            "updated_at": now,
        }
        self.storage.create_item_dir(item_id)
        self.storage.save_item(meta)
        return meta

    def promote_vod_file(
        self,
        vod_id: str,
        twitch_video_id: str,
        title: str,
        source_file: Path,
        container: str = "mp4",
        duration_seconds: Optional[float] = None,
        file_size_bytes: Optional[int] = None,
    ) -> dict:
        """Move a downloaded VOD file from the VOD dir into the library.

        Creates a new library item, moves the file, and returns the item
        metadata. Raises ``LibraryConflictError`` if an item for this
        ``twitch_video_id`` already exists.
        """
        if not source_file.is_file():
            raise LibraryValidationError(
                f"source file does not exist: {source_file}"
            )
        # Normalise the container so the recorded file_name matches the file
        # actually written by ``source_file_path`` (which rewrites unsupported
        # containers to mp4). Without this, a "mov" clip would be stored as
        # source.mp4 but recorded as source.mov and become unlocatable.
        source_extension = source_file.suffix.lstrip(".").lower()
        container = sanitize_container(
            source_extension if source_extension in SUPPORTED_CONTAINERS else container
        )
        # Duplication check.
        existing = self.find_by_twitch_video_id(twitch_video_id)
        if existing is not None:
            # If the existing item has no file on disk, overwrite it.
            existing_container = sanitize_container(existing.get("container") or container)
            existing_path = self.storage.source_file_path(existing["id"], existing_container)
            if not existing_path.is_file():
                self._move_file(source_file, existing_path)
                existing["file_name"] = existing_path.name
                existing["container"] = existing_container
                existing["file_size_bytes"] = file_size_bytes or existing_path.stat().st_size
                existing["duration_seconds"] = duration_seconds or existing.get("duration_seconds")
                existing["vod_id"] = vod_id
                existing["updated_at"] = _now_iso()
                self.storage.save_item(existing)
                return existing
            raise LibraryConflictError(
                f"A library item for twitch_video_id {twitch_video_id} already exists."
            )
        file_name = f"source.{container}"
        meta = self.create_item(
            source="vod",
            title=title,
            file_name=file_name,
            container=container,
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes,
            twitch_video_id=twitch_video_id,
            vod_id=vod_id,
        )
        dest = self.storage.source_file_path(meta["id"], container)
        self._move_file(source_file, dest)
        return meta

    def create_upload_item(
        self,
        file_name: str,
        title: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        lifecycle: str = LIFECYCLE_PERSISTENT,
        expires_at: Optional[str] = None,
    ) -> dict:
        """Create a library item for a manual file upload.

        Returns the metadata; the caller must write the actual file into
        the item directory (the file keeps its original name).

        The ``file_type`` (video/audio/image) is derived from the
        extension. Unknown extensions fall back to ``video`` with an
        ``mp4`` container for backward compatibility.
        """
        # Determine container/extension from the filename.
        ext = Path(file_name).suffix.lstrip(".").lower() or "mp4"
        container = sanitize_container(ext)
        meta = self.create_item(
            source="upload",
            title=title or file_name,
            file_name=file_name,
            container=container,
            duration_seconds=duration_seconds,
            lifecycle=lifecycle,
            expires_at=expires_at,
        )
        return meta

    @staticmethod
    def _move_file(src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dest))
        except (OSError, shutil.Error) as exc:
            raise LibraryStorageError(f"could not move {src} -> {dest}: {exc}") from exc

    # ------------------------------------------------------------------ read
    def list_items(
        self,
        *,
        include_temporary: bool = False,
        file_type: Optional[str] = None,
    ) -> list[dict]:
        self.cleanup_expired()
        if file_type is not None and file_type not in SUPPORTED_FILE_TYPES:
            raise LibraryValidationError(
                f"file_type must be one of {sorted(SUPPORTED_FILE_TYPES)}, got {file_type!r}"
            )
        results = list(self.storage.iter_items())
        if not include_temporary:
            results = [
                meta for meta in results
                if meta.get("lifecycle", LIFECYCLE_PERSISTENT) == LIFECYCLE_PERSISTENT
            ]
        for meta in results:
            self._ensure_file_type(meta)
            self.storage.enrich_with_file_info(meta)
        if file_type is not None:
            results = [meta for meta in results if meta.get("file_type") == file_type]
        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    def get_item(self, item_id: str) -> dict:
        meta = self.storage.load_item(item_id)
        if self._is_expired(meta):
            self.storage.delete_item(item_id)
            raise LibraryNotFoundError(f"library item expired: {item_id}")
        self._ensure_file_type(meta)
        self.storage.enrich_with_file_info(meta)
        return meta

    @staticmethod
    def _ensure_file_type(meta: dict) -> dict:
        """Backfill ``file_type`` on v1 items (lazy, in-memory only).

        v1 items written before the file-aware refactor have no
        ``file_type`` field. We derive it from the ``container``/
        ``file_name`` so callers always see a populated field without
        needing a migration. The one-time migration script upgrades
        items on disk; this helper covers items that haven't been
        migrated yet.
        """
        if meta.get("file_type") in SUPPORTED_FILE_TYPES:
            return meta
        derived = (
            file_type_for_filename(meta.get("file_name", ""))
            or file_type_for_extension(meta.get("container"))
            or FILE_TYPE_VIDEO
        )
        meta["file_type"] = derived
        return meta

    def promote_item(self, item_id: str) -> dict:
        """Promote a hidden temporary item into the persistent library."""
        meta = self.storage.load_item(item_id)
        if self._is_expired(meta):
            self.storage.delete_item(item_id)
            raise LibraryNotFoundError(f"library item expired: {item_id}")
        meta["lifecycle"] = LIFECYCLE_PERSISTENT
        meta["expires_at"] = None
        meta["updated_at"] = _now_iso()
        self.storage.save_item(meta)
        self._ensure_file_type(meta)
        self.storage.enrich_with_file_info(meta)
        return meta

    @staticmethod
    def _is_expired(meta: dict) -> bool:
        if meta.get("lifecycle", LIFECYCLE_PERSISTENT) != LIFECYCLE_TEMPORARY:
            return False
        # In-use items are never expired (referenced by an edit project).
        if meta.get("in_use"):
            return False
        raw = meta.get("expires_at")
        if not raw:
            return False
        try:
            expires = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        now = _dt.datetime.now(_dt.timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=_dt.timezone.utc)
        return expires <= now

    def mark_in_use(self, item_id: str) -> None:
        """Mark a library item as in-use so the cleanup loop skips it."""
        meta = self.storage.load_item(item_id)
        if meta.get("in_use"):
            return
        meta["in_use"] = True
        meta["updated_at"] = _now_iso()
        self.storage.save_item(meta)

    def unmark_in_use(self, item_id: str) -> None:
        """Clear the in-use flag so the cleanup loop can expire the item."""
        try:
            meta = self.storage.load_item(item_id)
        except Exception:
            return
        if not meta.get("in_use"):
            return
        meta["in_use"] = False
        meta["updated_at"] = _now_iso()
        self.storage.save_item(meta)

    def cleanup_expired(self) -> int:
        deleted = 0
        for meta in list(self.storage.iter_items()):
            if not self._is_expired(meta):
                continue
            try:
                if self.storage.delete_item(meta["id"]):
                    deleted += 1
            except Exception:
                logger.exception("Could not remove expired library item %s", meta.get("id"))
        return deleted

    def find_by_twitch_video_id(self, twitch_video_id: str) -> Optional[dict]:
        for meta in self.storage.iter_items():
            if meta.get("twitch_video_id") == twitch_video_id:
                return meta
        return None

    def find_by_vod_id(self, vod_id: str) -> Optional[dict]:
        for meta in self.storage.iter_items():
            if meta.get("vod_id") == vod_id:
                return meta
        return None

    # ------------------------------------------------------------------ update
    def unlink_vod(self, vod_id: str) -> Optional[dict]:
        """Set ``vod_id`` to null on the library item referencing this VOD.

        Called when a VOD is deleted (e.g. profile deletion). The library
        item survives with ``vod_id = null``.
        """
        meta = self.find_by_vod_id(vod_id)
        if meta is None:
            return None
        meta["vod_id"] = None
        meta["updated_at"] = _now_iso()
        self.storage.save_item(meta)
        return meta

    def link_vod(self, item_id: str, vod_id: str) -> dict:
        """Set the ``vod_id`` back-reference on a library item."""
        meta = self.storage.load_item(item_id)
        meta["vod_id"] = vod_id
        meta["updated_at"] = _now_iso()
        self.storage.save_item(meta)
        return meta

    # ------------------------------------------------------------------ delete
    def delete_item(self, item_id: str) -> bool:
        return self.storage.delete_item(item_id)

    # ------------------------------------------------------------------ file
    def item_file_path(self, item_id: str) -> Path:
        """Return the on-disk file path for a library item."""
        meta = self.storage.load_item(item_id)
        item_dir = self.storage.item_dir(item_id)
        file_name = meta.get("file_name", "")
        if not file_name:
            raise LibraryStorageError(f"item {item_id} has no file_name")
        path = item_dir / file_name
        if not path.is_file():
            raise LibraryNotFoundError(f"file not found for item {item_id}")
        return path
