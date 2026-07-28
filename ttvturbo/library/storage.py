"""Atomic, file-based persistence for library items.

Mirrors the safety guarantees of :mod:`vod_pipeline.storage`:
* ids must be valid canonical UUIDs (path-traversal protection);
* the resolved item directory must stay inside the library root;
* corrupt JSON files are logged and skipped during listing;
* an unknown ``schema_version`` is rejected;
* atomic writes via ``*.tmp`` -> ``os.replace``.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Iterator, Optional

from ttvturbo.storage_utils import (
    atomic_write_json,
    now_iso,
    read_json,
    safe_record_dir,
    validate_uuid,
)

from .schemas import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    LibraryNotFoundError,
    LibraryStorageError,
)

logger = logging.getLogger("ttvturbo.library.storage")

ITEM_FILENAME = "metadata.json"
TMP_SUFFIX = ".tmp"
SOURCE_BASENAME = "source"
SUPPORTED_CONTAINERS = ("mp4", "mkv", "webm")


def sanitize_container(container: str) -> str:
    """Normalise a container name to one of the supported on-disk extensions.

    ``source_file_path`` silently rewrites unsupported containers to ``mp4``,
    so callers that record the file name in metadata must apply the same
    rule — otherwise the recorded ``file_name`` won't match the file on disk
    and ``item_file_path`` will fail to locate it.
    """
    return container if container in SUPPORTED_CONTAINERS else "mp4"


def _now_iso() -> str:
    """Backward-compat wrapper around :func:`storage_utils.now_iso`."""
    return now_iso()


def _validate_uuid(value: str) -> str:
    """Backward-compat wrapper around :func:`storage_utils.validate_uuid`."""
    return validate_uuid(value, "item", LibraryStorageError)


class LibraryStorage:
    """Filesystem-backed store for library items."""

    def __init__(self, library_dir: Path) -> None:
        self.library_dir = Path(library_dir)
        self.library_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _item_dir(self, item_id: str) -> Path:
        return safe_record_dir(self.library_dir, item_id, "item", LibraryStorageError)

    def item_dir(self, item_id: str) -> Path:
        """Public accessor for the item directory (UUID-validated, traversal-safe).

        External callers (API modules, migration scripts, source resolver)
        should use this instead of the private ``_item_dir``.
        """
        return self._item_dir(item_id)

    def write_item_file(self, item_id: str, file_name: str, content: bytes) -> Path:
        """Write *content* to ``{item_dir}/{file_name}`` and return the path.

        Validates *file_name* for path-traversal safety.  The item directory
        is created if it does not exist.
        """
        if not file_name or "/" in file_name or "\\" in file_name:
            raise LibraryStorageError(f"invalid file_name: {file_name!r}")
        if file_name.startswith(".") or file_name.startswith("~"):
            raise LibraryStorageError(f"invalid file_name: {file_name!r}")
        dest = self._item_dir(item_id) / Path(file_name).name
        if dest.name != file_name:
            raise LibraryStorageError(f"invalid file_name: {file_name!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(content)
        return dest

    def _metadata_path(self, item_id: str) -> Path:
        return self._item_dir(item_id) / ITEM_FILENAME

    def source_file_path(self, item_id: str, container: str = "mp4") -> Path:
        """Return the canonical source file path for an item."""
        safe = sanitize_container(container)
        return self._item_dir(item_id) / f"{SOURCE_BASENAME}.{safe}"

    # ------------------------------------------------------------------ write
    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        atomic_write_json(path, payload, LibraryStorageError, kind="item")

    def save_item(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise LibraryStorageError("payload must be a dict")
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise LibraryStorageError(
                f"unsupported item schema_version {payload.get('schema_version')!r}"
            )
        if not payload.get("id"):
            raise LibraryStorageError("payload missing id")
        self._atomic_write_json(self._metadata_path(str(payload["id"])), payload)

    def create_item_dir(self, item_id: str) -> Path:
        """Create the item directory and return it."""
        d = self._item_dir(item_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------ read
    @staticmethod
    def _read_json(path: Path) -> dict:
        return read_json(path, LibraryStorageError, kind="item")

    def load_item(self, item_id: str) -> dict:
        path = self._metadata_path(item_id)
        if not path.is_file():
            raise LibraryNotFoundError(f"library item not found: {item_id}")
        payload = self._read_json(path)
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise LibraryStorageError(
                f"unknown item schema_version {payload.get('schema_version')!r}"
            )
        return payload

    def iter_items(self) -> Iterator[dict]:
        try:
            entries = list(self.library_dir.iterdir())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not scan library root %s: %s", self.library_dir, exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                _validate_uuid(entry.name)
            except LibraryStorageError:
                continue
            path = entry / ITEM_FILENAME
            if not path.is_file():
                continue
            try:
                payload = self._read_json(path)
            except LibraryStorageError as exc:
                logger.warning("Skipping unreadable library item %s: %s", path, exc)
                continue
            if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                logger.warning("Skipping library item %s: unknown schema_version", path)
                continue
            yield payload

    # ------------------------------------------------------------------ delete
    def delete_item(self, item_id: str) -> bool:
        item_dir = self._item_dir(item_id)
        if not item_dir.exists():
            return False
        tmp = item_dir.with_name(item_dir.name + ".deleting")
        try:
            os.replace(item_dir, tmp)
        except OSError as exc:
            raise LibraryStorageError(f"could not delete item {item_id}: {exc}") from exc
        shutil.rmtree(tmp, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ helpers
    def enrich_with_file_info(self, meta: dict) -> dict:
        """Add file_size_bytes and file_exists to a metadata dict."""
        item_dir = self._item_dir(meta["id"])
        file_name = meta.get("file_name", "")
        src = item_dir / file_name if file_name else None
        if src and src.is_file():
            meta["file_size_bytes"] = src.stat().st_size
            meta["file_exists"] = True
        else:
            meta["file_size_bytes"] = None
            meta["file_exists"] = False
        return meta
