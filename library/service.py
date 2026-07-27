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
    LibraryConflictError,
    LibraryNotFoundError,
    LibraryStorageError,
    LibraryValidationError,
)
from .storage import LibraryStorage, _now_iso

logger = logging.getLogger("ttvturbo.library.service")


class LibraryService:
    """Business logic for the persistent library."""

    def __init__(self, storage: LibraryStorage) -> None:
        self.storage = storage

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
    ) -> dict:
        """Create a new library item record (metadata only).

        The caller is responsible for placing the source file into the
        item directory (typically via ``storage.source_file_path(id, container)``).
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
            "container": container,
            "twitch_video_id": twitch_video_id,
            "vod_id": vod_id,
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
        # Duplication check.
        existing = self.find_by_twitch_video_id(twitch_video_id)
        if existing is not None:
            # If the existing item has no file on disk, overwrite it.
            existing_path = self.storage.source_file_path(
                existing["id"], existing.get("container") or container
            )
            if not existing_path.is_file():
                self._move_file(source_file, existing_path)
                existing["file_name"] = existing_path.name
                existing["container"] = container
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
    ) -> dict:
        """Create a library item for a manual file upload.

        Returns the metadata; the caller must write the actual file into
        the item directory (the file keeps its original name).
        """
        # Determine container from extension.
        ext = Path(file_name).suffix.lstrip(".").lower() or "mp4"
        container = ext if ext in ("mp4", "mkv", "webm") else "mp4"
        meta = self.create_item(
            source="upload",
            title=title or file_name,
            file_name=file_name,
            container=container,
            duration_seconds=duration_seconds,
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
    def list_items(self) -> list[dict]:
        results = list(self.storage.iter_items())
        for meta in results:
            self.storage.enrich_with_file_info(meta)
        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    def get_item(self, item_id: str) -> dict:
        meta = self.storage.load_item(item_id)
        self.storage.enrich_with_file_info(meta)
        return meta

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
        item_dir = self.storage._item_dir(item_id)  # noqa: SLF001
        file_name = meta.get("file_name", "")
        if not file_name:
            raise LibraryStorageError(f"item {item_id} has no file_name")
        path = item_dir / file_name
        if not path.is_file():
            raise LibraryNotFoundError(f"file not found for item {item_id}")
        return path
