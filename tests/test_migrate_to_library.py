"""Tests for the migrate_to_library migration tool.

Verifies:
1. VODs with status=READY are migrated to the library.
2. Uploads are migrated to the library.
3. Re-running skips already-migrated items.
4. --dry-run does not move files or write metadata.
5. --backup creates .bak copies of VOD metadata.
6. Corrupt metadata is skipped without crashing.
7. VODs without a source file are skipped.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from migrate_to_library import migrate_uploads, migrate_vods


def _write_vod_meta(
    vods_dir: Path,
    vod_id: str,
    *,
    status: str = "READY",
    file_name: str = "source.mp4",
    library_item_id: str | None = None,
    twitch_video_id: str = "",
) -> Path:
    """Write a VOD metadata.json and return the VOD directory."""
    vod_dir = vods_dir / vod_id
    vod_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": vod_id,
        "schema_version": 1,
        "status": status,
        "twitch_video_id": twitch_video_id,
        "title": f"VOD {vod_id[:8]}",
        "download": {
            "file_name": file_name,
            "container": "mp4",
            "duration_seconds": 60.0,
            "file_size_bytes": 1024,
        },
    }
    if library_item_id:
        meta["library_item_id"] = library_item_id
    with open(vod_dir / "metadata.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return vod_dir


def _write_upload_meta(
    uploads_dir: Path,
    upload_id: str,
    *,
    file_name: str = "upload.mp4",
) -> Path:
    """Write an upload metadata.json and return the upload directory."""
    upload_dir = uploads_dir / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": upload_id,
        "schema_version": 1,
        "file_name": file_name,
        "title": f"Upload {upload_id[:8]}",
        "duration_seconds": 30.0,
    }
    with open(upload_dir / "metadata.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return upload_dir


@pytest.fixture()
def library_service(tmp_path: Path):
    from library import LibraryService, LibraryStorage
    return LibraryService(LibraryStorage(tmp_path / "library"))


# ---------------------------------------------------------------------------
# VOD migration
# ---------------------------------------------------------------------------


class TestMigrateVods:
    def test_migrates_ready_vod(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        vod_dir = _write_vod_meta(vods_dir, vod_id, twitch_video_id="12345")
        # Create the source file.
        src = vod_dir / "source.mp4"
        src.write_bytes(b"fake video content")

        count = migrate_vods(tmp_path, library_service)
        assert count == 1

        # VOD metadata should have library_item_id set.
        with open(vod_dir / "metadata.json", encoding="utf-8") as fh:
            meta = json.load(fh)
        assert meta["library_item_id"]

        # The source file should be gone from the VOD dir.
        assert not src.is_file()

        # The library item should exist.
        item = library_service.get_item(meta["library_item_id"])
        assert item["twitch_video_id"] == "12345"

    def test_skips_already_migrated(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        existing_item_id = str(uuid.uuid4())
        _write_vod_meta(vods_dir, vod_id, library_item_id=existing_item_id)
        count = migrate_vods(tmp_path, library_service)
        assert count == 0

    def test_skips_not_ready_vod(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        _write_vod_meta(vods_dir, vod_id, status="DOWNLOADING")
        count = migrate_vods(tmp_path, library_service)
        assert count == 0

    def test_skips_missing_source_file(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        _write_vod_meta(vods_dir, vod_id)
        # No source file created.
        count = migrate_vods(tmp_path, library_service)
        assert count == 0

    def test_skips_corrupt_metadata(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        vod_dir = vods_dir / vod_id
        vod_dir.mkdir(parents=True)
        (vod_dir / "metadata.json").write_text("{not valid json", encoding="utf-8")
        count = migrate_vods(tmp_path, library_service)
        assert count == 0

    def test_dry_run_does_not_move_files(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        vod_dir = _write_vod_meta(vods_dir, vod_id, twitch_video_id="999")
        src = vod_dir / "source.mp4"
        src.write_bytes(b"fake video content")

        count = migrate_vods(tmp_path, library_service, dry_run=True)
        assert count == 1

        # Source file should still be in the VOD dir.
        assert src.is_file()
        # VOD metadata should NOT have library_item_id.
        with open(vod_dir / "metadata.json", encoding="utf-8") as fh:
            meta = json.load(fh)
        assert "library_item_id" not in meta
        # No library items created.
        assert library_service.list_items() == []

    def test_backup_creates_bak_file(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        vod_dir = _write_vod_meta(vods_dir, vod_id, twitch_video_id="777")
        src = vod_dir / "source.mp4"
        src.write_bytes(b"fake video content")

        migrate_vods(tmp_path, library_service, backup=True)

        bak = vod_dir / "metadata.json.bak"
        assert bak.is_file()
        # The backup should contain the original metadata (no library_item_id).
        with open(bak, encoding="utf-8") as fh:
            bak_meta = json.load(fh)
        assert "library_item_id" not in bak_meta

    def test_rerun_links_to_existing_library_item(self, tmp_path: Path, library_service):
        vods_dir = tmp_path / "vods"
        vods_dir.mkdir()
        vod_id = str(uuid.uuid4())
        vod_dir = _write_vod_meta(vods_dir, vod_id, twitch_video_id="555")
        src = vod_dir / "source.mp4"
        src.write_bytes(b"fake video content")

        # First migration.
        migrate_vods(tmp_path, library_service)
        with open(vod_dir / "metadata.json", encoding="utf-8") as fh:
            meta = json.load(fh)
        item_id = meta["library_item_id"]

        # Simulate a second VOD with the same twitch_video_id.
        vod_id2 = str(uuid.uuid4())
        vod_dir2 = _write_vod_meta(vods_dir, vod_id2, twitch_video_id="555")
        src2 = vod_dir2 / "source.mp4"
        src2.write_bytes(b"second video")

        # Second migration should link to the existing item.
        count = migrate_vods(tmp_path, library_service)
        assert count == 1
        with open(vod_dir2 / "metadata.json", encoding="utf-8") as fh:
            meta2 = json.load(fh)
        assert meta2["library_item_id"] == item_id


# ---------------------------------------------------------------------------
# Upload migration
# ---------------------------------------------------------------------------


class TestMigrateUploads:
    def test_migrates_upload(self, tmp_path: Path, library_service):
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        upload_id = str(uuid.uuid4())
        upload_dir = _write_upload_meta(uploads_dir, upload_id)
        src = upload_dir / "upload.mp4"
        src.write_bytes(b"fake upload content")

        count = migrate_uploads(tmp_path, library_service)
        assert count == 1

        # Source file should be gone from the upload dir.
        assert not src.is_file()

        # A library item should exist.
        items = library_service.list_items()
        assert len(items) == 1
        assert items[0]["file_name"] == "upload.mp4"

    def test_skips_missing_file(self, tmp_path: Path, library_service):
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        upload_id = str(uuid.uuid4())
        _write_upload_meta(uploads_dir, upload_id)
        # No file created.
        count = migrate_uploads(tmp_path, library_service)
        assert count == 0

    def test_skips_corrupt_metadata(self, tmp_path: Path, library_service):
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        upload_id = str(uuid.uuid4())
        upload_dir = uploads_dir / upload_id
        upload_dir.mkdir()
        (upload_dir / "metadata.json").write_text("{bad json", encoding="utf-8")
        count = migrate_uploads(tmp_path, library_service)
        assert count == 0

    def test_dry_run_does_not_move_files(self, tmp_path: Path, library_service):
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        upload_id = str(uuid.uuid4())
        upload_dir = _write_upload_meta(uploads_dir, upload_id)
        src = upload_dir / "upload.mp4"
        src.write_bytes(b"fake upload content")

        count = migrate_uploads(tmp_path, library_service, dry_run=True)
        assert count == 1

        # Source file should still be in the upload dir.
        assert src.is_file()
        # No library items created.
        assert library_service.list_items() == []

    def test_no_uploads_dir(self, tmp_path: Path, library_service):
        count = migrate_uploads(tmp_path, library_service)
        assert count == 0
