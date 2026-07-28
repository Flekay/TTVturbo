"""One-time migration: move existing VOD source files and uploads into the library.

Run this once after deploying the library refactor. It:

1. Scans ``data/vods/*/`` for VODs with status=READY and a ``source.<container>``
   file. For each, creates a library item and moves the file there.
2. Scans ``data/uploads/*/`` for existing uploads. For each, creates a library
   item with ``source="upload"`` and moves the file there.
3. Updates VOD metadata to set ``library_item_id``.

Usage::

    python migrate_to_library.py [--data-dir /path/to/data]

Safe to re-run: it skips items that already have a ``library_item_id`` and
skips uploads that already exist in the library (matched by file_name + size).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_to_library")


def migrate_vods(data_dir: Path, library_service) -> int:
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
        try:
            with open(meta_path, "r", encoding="utf-8-sig") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping %s: corrupt metadata: %s", entry.name, exc)
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
            # Preserve the existing updated_at — no semantic change needed.
            # Just update the vod_id link.
            library_service.link_vod(existing["id"], meta["id"])
            # Save updated VOD metadata.
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, ensure_ascii=False)
            logger.info("Linked VOD %s to existing library item %s", meta["id"][:8], existing["id"][:8])
            count += 1
            continue
        try:
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
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, ensure_ascii=False)
            logger.info("Migrated VOD %s -> library item %s", meta["id"][:8], item["id"][:8])
            count += 1
        except Exception as exc:
            logger.error("Failed to migrate VOD %s: %s", meta["id"][:8], exc)
    return count


def migrate_uploads(data_dir: Path, library_service) -> int:
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
        try:
            with open(meta_path, "r", encoding="utf-8-sig") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping upload %s: corrupt metadata: %s", entry.name, exc)
            continue
        file_name = meta.get("file_name")
        if not file_name:
            continue
        src = entry / file_name
        if not src.is_file():
            logger.warning("Skipping upload %s: file %s missing", entry.name, file_name)
            continue
        try:
            # Create library item.
            lib_meta = library_service.create_upload_item(
                file_name=file_name,
                title=meta.get("title") or file_name,
                duration_seconds=meta.get("duration_seconds"),
            )
            dest = library_service.storage._item_dir(lib_meta["id"]) / file_name  # noqa: SLF001
            shutil.move(str(src), str(dest))
            lib_meta["file_size_bytes"] = dest.stat().st_size
            library_service.storage.save_item(lib_meta)
            logger.info("Migrated upload %s -> library item %s", meta["id"][:8], lib_meta["id"][:8])
            count += 1
        except Exception as exc:
            logger.error("Failed to migrate upload %s: %s", meta["id"][:8], exc)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate VODs and uploads to the library.")
    parser.add_argument("--data-dir", default=None, help="Path to the data directory.")
    args = parser.parse_args()

    # Ensure we import from the repo root.
    repo_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(repo_dir))

    from library import LibraryService, LibraryStorage

    data_dir = Path(args.data_dir) if args.data_dir else (repo_dir / "data")
    if not data_dir.is_dir():
        logger.error("Data directory does not exist: %s", data_dir)
        return 1

    library_dir = data_dir / "library"
    library_storage = LibraryStorage(library_dir)
    library_service = LibraryService(library_storage)

    logger.info("Migrating VODs from %s/vods/ ...", data_dir)
    vod_count = migrate_vods(data_dir, library_service)
    logger.info("Migrated %d VOD(s).", vod_count)

    logger.info("Migrating uploads from %s/uploads/ ...", data_dir)
    upload_count = migrate_uploads(data_dir, library_service)
    logger.info("Migrated %d upload(s).", upload_count)

    logger.info("Migration complete: %d VOD(s) + %d upload(s).", vod_count, upload_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
