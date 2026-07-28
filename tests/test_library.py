"""Tests for the library service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ttvturbo.library import (
    LibraryConflictError,
    LibraryNotFoundError,
    LibraryService,
    LibraryStorage,
    LibraryValidationError,
)


@pytest.fixture
def library_service(tmp_path: Path) -> LibraryService:
    storage = LibraryStorage(tmp_path / "library")
    return LibraryService(storage)


def test_create_item(library_service):
    item = library_service.create_item(
        source="vod",
        title="Test VOD",
        file_name="source.mp4",
        container="mp4",
        twitch_video_id="12345",
        vod_id="vod-uuid-1",
    )
    assert item["id"]
    assert item["source"] == "vod"
    assert item["title"] == "Test VOD"
    assert item["twitch_video_id"] == "12345"
    assert item["vod_id"] == "vod-uuid-1"
    # Can be loaded back.
    loaded = library_service.get_item(item["id"])
    assert loaded["id"] == item["id"]


def test_create_item_invalid_source(library_service):
    with pytest.raises(LibraryValidationError):
        library_service.create_item(
            source="invalid",
            title="Test",
            file_name="source.mp4",
        )


def test_find_by_twitch_video_id(library_service):
    library_service.create_item(
        source="vod",
        title="VOD 1",
        file_name="source.mp4",
        twitch_video_id="100",
    )
    found = library_service.find_by_twitch_video_id("100")
    assert found is not None
    assert found["title"] == "VOD 1"
    assert library_service.find_by_twitch_video_id("999") is None


def test_duplication_check(library_service):
    library_service.create_item(
        source="vod",
        title="VOD 1",
        file_name="source.mp4",
        twitch_video_id="100",
    )
    with pytest.raises(LibraryConflictError):
        library_service.create_item(
            source="vod",
            title="VOD 2",
            file_name="source.mp4",
            twitch_video_id="100",
        )


def test_promote_vod_file(library_service, tmp_path):
    # Create a fake VOD source file.
    vod_dir = tmp_path / "vods" / "test-vod"
    vod_dir.mkdir(parents=True)
    src = vod_dir / "source.mp4"
    src.write_bytes(b"fake video content")
    item = library_service.promote_vod_file(
        vod_id="vod-uuid-1",
        twitch_video_id="200",
        title="Test VOD",
        source_file=src,
        container="mp4",
        duration_seconds=60.0,
        file_size_bytes=len(b"fake video content"),
    )
    assert item["source"] == "vod"
    assert item["twitch_video_id"] == "200"
    assert item["vod_id"] == "vod-uuid-1"
    # Source file moved to library.
    assert not src.exists()
    lib_file = library_service.item_file_path(item["id"])
    assert lib_file.is_file()
    assert lib_file.read_bytes() == b"fake video content"


def test_promote_vod_file_duplicate(library_service, tmp_path):
    # First promotion.
    vod_dir = tmp_path / "vods" / "test-vod"
    vod_dir.mkdir(parents=True)
    src1 = vod_dir / "source.mp4"
    src1.write_bytes(b"first")
    library_service.promote_vod_file(
        vod_id="vod-1",
        twitch_video_id="300",
        title="VOD 1",
        source_file=src1,
    )
    # Second promotion with same twitch_video_id should fail.
    src2 = vod_dir / "source2.mp4"
    src2.write_bytes(b"second")
    with pytest.raises(LibraryConflictError):
        library_service.promote_vod_file(
            vod_id="vod-2",
            twitch_video_id="300",
            title="VOD 2",
            source_file=src2,
        )


def test_promote_vod_file_sanitises_unsupported_container(library_service, tmp_path):
    """ffprobe reports ``mov`` for MP4 files (format_name ``mov,mp4,...``).

    ``source_file_path`` rewrites unsupported containers to ``mp4``, so the
    recorded ``file_name`` must be sanitised the same way — otherwise the
    item would record ``source.mov`` while the file lands at ``source.mp4``
    and ``item_file_path`` could never locate it.
    """
    vod_dir = tmp_path / "vods" / "test-vod"
    vod_dir.mkdir(parents=True)
    src = vod_dir / "source.mp4"
    src.write_bytes(b"fake video content")
    item = library_service.promote_vod_file(
        vod_id="vod-uuid-1",
        twitch_video_id="400",
        title="Test VOD",
        source_file=src,
        container="mov",  # ffprobe's format_name for mp4 starts with "mov"
        duration_seconds=60.0,
        file_size_bytes=len(b"fake video content"),
    )
    # The container must be normalised to a supported extension.
    assert item["container"] == "mp4"
    assert item["file_name"] == "source.mp4"
    # And the file must be locatable via item_file_path.
    lib_file = library_service.item_file_path(item["id"])
    assert lib_file.is_file()
    assert lib_file.name == "source.mp4"
    assert lib_file.read_bytes() == b"fake video content"


def test_unlink_vod(library_service):
    item = library_service.create_item(
        source="vod",
        title="VOD",
        file_name="source.mp4",
        vod_id="vod-uuid-1",
    )
    result = library_service.unlink_vod("vod-uuid-1")
    assert result is not None
    assert result["vod_id"] is None
    # Item still exists.
    loaded = library_service.get_item(item["id"])
    assert loaded["vod_id"] is None


def test_unlink_vod_not_found(library_service):
    assert library_service.unlink_vod("nonexistent") is None


def test_delete_item(library_service):
    item = library_service.create_item(
        source="upload",
        title="Upload",
        file_name="test.mp4",
    )
    assert library_service.delete_item(item["id"]) is True
    with pytest.raises(LibraryNotFoundError):
        library_service.get_item(item["id"])


def test_list_items_sorted_by_date(library_service):
    item1 = library_service.create_item(
        source="vod", title="Older", file_name="source.mp4"
    )
    # Manually set an older timestamp on item1.
    item1["created_at"] = "2025-01-01T00:00:00+00:00"
    library_service.storage.save_item(item1)
    item2 = library_service.create_item(
        source="vod", title="Newer", file_name="source.mp4",
        twitch_video_id="999"  # different to avoid conflict
    )
    items = library_service.list_items()
    assert items[0]["title"] == "Newer"
    assert items[1]["title"] == "Older"


def test_create_upload_item(library_service):
    item = library_service.create_upload_item(
        file_name="my_video.mp4",
        title="My Video",
    )
    assert item["source"] == "upload"
    assert item["file_name"] == "my_video.mp4"
    assert item["title"] == "My Video"


def test_create_upload_item_default_title(library_service):
    item = library_service.create_upload_item(file_name="video.mkv")
    assert item["title"] == "video.mkv"
    assert item["container"] == "mkv"


def test_item_file_path_missing(library_service):
    item = library_service.create_item(
        source="vod", title="VOD", file_name="source.mp4"
    )
    with pytest.raises(LibraryNotFoundError):
        library_service.item_file_path(item["id"])
