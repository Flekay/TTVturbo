"""One-time migration: upgrade library items from schema v1 to v2.

Schema v2 introduced the ``file_type`` field (``video`` | ``audio`` |
``image``) so the library is file-aware and can sort/filter by media
type. v1 items have no ``file_type``; this script backfills it from
the ``container``/``file_name`` extension and bumps ``schema_version``
to 2 on disk.

Safe to re-run: items already at v2 (with a valid ``file_type``) are
skipped.

Usage::

    python migrate_library_file_types.py [--data-dir /path/to/data] [--dry-run] [--backup]

``--dry-run`` prints what would be migrated without writing any
metadata. ``--backup`` creates a ``.bak`` copy of each metadata.json
before modifying it.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from ttvturbo.storage_utils import atomic_write_json, read_json_optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_library_file_types")


def migrate_library_items(
    library_dir: Path,
    *,
    dry_run: bool = False,
    backup: bool = False,
) -> tuple[int, int]:
    """Upgrade v1 library items to v2 in place.

    Returns ``(upgraded, skipped)``.
    """
    from ttvturbo.library import (
        SCHEMA_VERSION,
        SUPPORTED_FILE_TYPES,
        file_type_for_extension,
        file_type_for_filename,
    )
    from ttvturbo.library.schemas import LibraryStorageError

    if not library_dir.is_dir():
        logger.info("No library directory found at %s, nothing to migrate.", library_dir)
        return 0, 0

    upgraded = 0
    skipped = 0
    for entry in library_dir.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if not meta_path.is_file():
            continue
        meta = read_json_optional(meta_path)
        if meta is None:
            logger.warning("Skipping %s: corrupt metadata", entry.name)
            skipped += 1
            continue
        current_version = meta.get("schema_version")
        existing_type = meta.get("file_type")
        # Skip if already v2 with a valid file_type.
        if current_version == SCHEMA_VERSION and existing_type in SUPPORTED_FILE_TYPES:
            skipped += 1
            continue
        # Derive file_type from filename > container, default video.
        derived = (
            file_type_for_filename(meta.get("file_name", ""))
            or file_type_for_extension(meta.get("container"))
            or "video"
        )
        meta["schema_version"] = SCHEMA_VERSION
        meta["file_type"] = derived
        if dry_run:
            logger.info(
                "[dry-run] Would upgrade %s -> v2 (file_type=%s)",
                entry.name[:8],
                derived,
            )
            upgraded += 1
            continue
        if backup and meta_path.is_file():
            bak = meta_path.with_suffix(".json.bak")
            if not bak.exists():
                shutil.copy2(meta_path, bak)
        try:
            atomic_write_json(meta_path, meta, LibraryStorageError, kind="item")
        except Exception as exc:
            logger.error("Failed to upgrade %s: %s", entry.name[:8], exc)
            skipped += 1
            continue
        logger.info("Upgraded %s -> v2 (file_type=%s)", entry.name[:8], derived)
        upgraded += 1
    return upgraded, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate library items from schema v1 to v2 (add file_type)."
    )
    parser.add_argument("--data-dir", default=None, help="Path to the data directory.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be migrated without writing metadata.")
    parser.add_argument("--backup", action="store_true", help="Create .bak copies of metadata before modifying.")
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_dir))

    data_dir = Path(args.data_dir) if args.data_dir else (repo_dir / "data")
    if not data_dir.is_dir():
        logger.error("Data directory does not exist: %s", data_dir)
        return 1

    library_dir = data_dir / "library"
    if args.dry_run:
        logger.info("DRY RUN — no metadata will be modified.")

    logger.info("Migrating library items in %s ...", library_dir)
    upgraded, skipped = migrate_library_items(
        library_dir, dry_run=args.dry_run, backup=args.backup
    )
    logger.info("Upgraded %d item(s), skipped %d.", upgraded, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
