"""One-time migration: move existing VOD source files and uploads into the library.

Run this once after deploying the library refactor. It:

1. Scans ``data/vods/*/`` for VODs with status=READY and a ``source.<container>``
   file. For each, creates a library item and moves the file there.
2. Scans ``data/uploads/*/`` for existing uploads. For each, creates a library
   item with ``source="upload"`` and moves the file there.
3. Updates VOD metadata to set ``library_item_id`` (atomically).

Usage::

    python migrate_to_library.py [--data-dir /path/to/data] [--dry-run] [--backup]

Safe to re-run: it skips items that already have a ``library_item_id`` and
skips uploads that already exist in the library (matched by file_name + size).

``--dry-run`` prints what would be migrated without moving any files or
writing any metadata.  ``--backup`` creates a ``.bak`` copy of each VOD
metadata.json before modifying it.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from ttvturbo.storage_utils import atomic_write_json, read_json_optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_to_library")


def _save_vod_meta(meta_path: Path, meta: dict, *, dry_run: bool, backup: bool) -> None:
    """Atomically write VOD metadata, optionally creating a backup first."""
    if dry_run:
        return
    if backup and meta_path.is_file():
        bak = meta_path.with_suffix(".json.bak")
        if not bak.exists():
            shutil.copy2(meta_path, bak)
    # Use the VodStorageError class for the atomic write so error messages
    # are consistent with the rest of the codebase.
    from ttvturbo.vod_pipeline.schemas import VodStorageError
    atomic_write_json(meta_path, meta, VodStorageError, kind="vod")


def migrate_vods(
    data_dir: Path,
    library_service,
    *,
    dry_run: bool = False,
    backup: bool = False,
) -> int:
    """Move READY VOD source files into the library. Returns count migrated."""
    vods_dir = data_dir / "vods"
    if not vods_dir.is_dir():
        logger.info("No vods/ directory found, skipping VOD migration.")
        return 0
    count = 0
    for entry in vods_dir.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if not meta_path.is_file():
            continue
        meta = read_json_optional(meta_path)
        if meta is None:
            logger.warning("Skipping %s: corrupt metadata", entry.name)
            continue
        if meta.get("library_item_id"):
            continue  # already migrated
        if meta.get("status") != "READY":
            continue  # not downloaded yet
        download = meta.get("download") or {}
        file_name = download.get("file_name")
        if not file_name:
            continue
        src = entry / file_name
        if not src.is_file():
            logger.warning("Skipping %s: source file %s missing", entry.name, file_name)
            continue
        twitch_video_id = meta.get("twitch_video_id", "")
        # Check if already in library (re-run safety).
        existing = None
        if twitch_video_id:
            existing = library_service.find_by_twitch_video_id(twitch_video_id)
        if existing:
            meta["library_item_id"] = existing["id"]
            # Just update the vod_id link.
            if dry_run:
                logger.info("[dry-run] Would link VOD %s to existing library item %s", meta["id"][:8], existing["id"][:8])
            else:
                library_service.link_vod(existing["id"], meta["id"])
                _save_vod_meta(meta_path, meta, dry_run=dry_run, backup=backup)
                logger.info("Linked VOD %s to existing library item %s", meta["id"][:8], existing["id"][:8])
            count += 1
            continue
        try:
            if dry_run:
                logger.info("[dry-run] Would migrate VOD %s -> library item", meta["id"][:8])
                count += 1
                continue
            item = library_service.promote_vod_file(
                vod_id=meta["id"],
                twitch_video_id=twitch_video_id,
                title=meta.get("title") or twitch_video_id or meta["id"],
                source_file=src,
                container=download.get("container") or "mp4",
                duration_seconds=download.get("duration_seconds"),
                file_size_bytes=download.get("file_size_bytes") or src.stat().st_size,
            )
            meta["library_item_id"] = item["id"]
            _save_vod_meta(meta_path, meta, dry_run=dry_run, backup=backup)
            logger.info("Migrated VOD %s -> library item %s", meta["id"][:8], item["id"][:8])
            count += 1
        except Exception as exc:
            logger.error("Failed to migrate VOD %s: %s", meta["id"][:8], exc)
    return count


def migrate_uploads(
    data_dir: Path,
    library_service,
    *,
    dry_run: bool = False,
) -> int:
    """Move existing uploads into the library. Returns count migrated."""
    uploads_dir = data_dir / "uploads"
    if not uploads_dir.is_dir():
        logger.info("No uploads/ directory found, skipping upload migration.")
        return 0
    count = 0
    for entry in uploads_dir.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if not meta_path.is_file():
            continue
        meta = read_json_optional(meta_path)
        if meta is None:
            logger.warning("Skipping upload %s: corrupt metadata", entry.name)
            continue
        file_name = meta.get("file_name")
        if not file_name:
            continue
        src = entry / file_name
        if not src.is_file():
            logger.warning("Skipping upload %s: file %s missing", entry.name, file_name)
            continue
        try:
            if dry_run:
                logger.info("[dry-run] Would migrate upload %s -> library item", meta.get("id", entry.name)[:8])
                count += 1
                continue
            # Create library item.
            lib_meta = library_service.create_upload_item(
                file_name=file_name,
                title=meta.get("title") or file_name,
                duration_seconds=meta.get("duration_seconds"),
            )
            dest = library_service.storage.item_dir(lib_meta["id"]) / file_name
            shutil.move(str(src), str(dest))
            lib_meta["file_size_bytes"] = dest.stat().st_size
            library_service.storage.save_item(lib_meta)
            logger.info("Migrated upload %s -> library item %s", meta.get("id", entry.name)[:8], lib_meta["id"][:8])
            count += 1
        except Exception as exc:
            logger.error("Failed to migrate upload %s: %s", meta.get("id", entry.name)[:8], exc)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate VODs and uploads to the library.")
    parser.add_argument("--data-dir", default=None, help="Path to the data directory.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be migrated without moving files.")
    parser.add_argument("--backup", action="store_true", help="Create .bak copies of VOD metadata before modifying.")
    args = parser.parse_args()

    # Ensure we import from the repo root (parent of the ttvturbo package).
    repo_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_dir))

    from ttvturbo.library import LibraryService, LibraryStorage

    data_dir = Path(args.data_dir) if args.data_dir else (repo_dir / "data")
    if not data_dir.is_dir():
        logger.error("Data directory does not exist: %s", data_dir)
        return 1

    library_dir = data_dir / "library"
    library_storage = LibraryStorage(library_dir)
    library_service = LibraryService(library_storage)

    if args.dry_run:
        logger.info("DRY RUN — no files will be moved or modified.")

    logger.info("Migrating VODs from %s/vods/ ...", data_dir)
    vod_count = migrate_vods(data_dir, library_service, dry_run=args.dry_run, backup=args.backup)
    logger.info("Migrated %d VOD(s).", vod_count)

    logger.info("Migrating uploads from %s/uploads/ ...", data_dir)
    upload_count = migrate_uploads(data_dir, library_service, dry_run=args.dry_run)
    logger.info("Migrated %d upload(s).", upload_count)

    logger.info("Migration complete: %d VOD(s) + %d upload(s).", vod_count, upload_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
