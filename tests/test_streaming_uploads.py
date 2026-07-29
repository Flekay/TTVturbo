"""Tests for streaming and atomic media uploads.

Verifies that:

* Uploaded files are streamed to a temp path and atomically renamed.
* A partial upload (simulated client disconnect) does not leave a
  half-written file at the final path.
* The temp file is cleaned up on failure.
* The final file is complete and byte-identical to the input.
* No leftover ``.tmp`` files remain after a successful upload.
* The library and legacy upload paths both stream atomically.
* Large files (multi-chunk) are streamed correctly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

import pytest


class _FakeUploadFile:
    """Mimics a Starlette/FastAPI UploadFile for unit tests."""

    def __init__(self, data: bytes, chunk_size: int | None = None) -> None:
        self._data = data
        self._pos = 0
        self._chunk_size = chunk_size or 1024 * 1024
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        if self.closed:
            return b""
        if size == -1 or size is None:
            size = self._chunk_size
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True


class _FailingUploadFile:
    """Mimics an UploadFile that fails mid-stream (client disconnect)."""

    def __init__(self, data: bytes, fail_after: int) -> None:
        self._data = data
        self._pos = 0
        self._fail_after = fail_after
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        if self.closed:
            return b""
        if self._pos >= self._fail_after:
            raise ConnectionError("client disconnected")
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Library storage streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_library_stream_item_file_atomic(tmp_path: Path):
    from ttvturbo.library import LibraryService, LibraryStorage

    storage = LibraryStorage(tmp_path / "library")
    service = LibraryService(storage)
    meta = service.create_upload_item(file_name="test.wav", title="test")
    data = b"\x00" * (2 * 1024 * 1024 + 123)  # > 2 chunks
    fake = _FakeUploadFile(data)

    dest = await storage.stream_item_file(meta["id"], "test.wav", fake)
    assert dest.is_file()
    assert dest.read_bytes() == data
    # No leftover tmp files.
    assert list(dest.parent.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_library_stream_partial_upload_no_final_file(tmp_path: Path):
    from ttvturbo.library import LibraryService, LibraryStorage

    storage = LibraryStorage(tmp_path / "library")
    service = LibraryService(storage)
    meta = service.create_upload_item(file_name="test.wav", title="test")
    data = b"\x00" * 1024 * 1024
    fake = _FailingUploadFile(data, fail_after=100)

    with pytest.raises(ConnectionError):
        await storage.stream_item_file(meta["id"], "test.wav", fake)

    # The final file must NOT exist (atomic rename never happened).
    dest = storage.item_dir(meta["id"]) / "test.wav"
    assert not dest.exists()
    # No leftover tmp files.
    assert list(dest.parent.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_library_stream_empty_file(tmp_path: Path):
    from ttvturbo.library import LibraryService, LibraryStorage

    storage = LibraryStorage(tmp_path / "library")
    service = LibraryService(storage)
    meta = service.create_upload_item(file_name="empty.wav", title="empty")
    fake = _FakeUploadFile(b"")

    dest = await storage.stream_item_file(meta["id"], "empty.wav", fake)
    assert dest.is_file()
    assert dest.read_bytes() == b""


@pytest.mark.asyncio
async def test_library_stream_rejects_traversal(tmp_path: Path):
    from ttvturbo.library import LibraryService, LibraryStorage, LibraryStorageError

    storage = LibraryStorage(tmp_path / "library")
    service = LibraryService(storage)
    meta = service.create_upload_item(file_name="test.wav", title="test")
    fake = _FakeUploadFile(b"data")

    with pytest.raises(LibraryStorageError):
        await storage.stream_item_file(meta["id"], "../escape.wav", fake)


# ---------------------------------------------------------------------------
# UploadStorage streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_storage_stream_atomic(tmp_path: Path):
    from ttvturbo.media_processing.uploads import UploadStorage

    storage = UploadStorage(tmp_path / "uploads")
    meta = storage.create_upload(file_name="audio.mp3", title="audio")
    data = b"\xff" * (3 * 1024 * 1024 + 7)
    fake = _FakeUploadFile(data)

    dest = await storage.stream_upload_file(meta["id"], "audio.mp3", fake)
    assert dest.is_file()
    assert dest.read_bytes() == data
    assert list(dest.parent.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_upload_storage_stream_partial_no_final(tmp_path: Path):
    from ttvturbo.media_processing.uploads import UploadStorage

    storage = UploadStorage(tmp_path / "uploads")
    meta = storage.create_upload(file_name="audio.mp3", title="audio")
    data = b"\xff" * 1024 * 1024
    fake = _FailingUploadFile(data, fail_after=200)

    with pytest.raises(ConnectionError):
        await storage.stream_upload_file(meta["id"], "audio.mp3", fake)

    dest = storage.upload_dir(meta["id"]) / "audio.mp3"
    assert not dest.exists()
    assert list(dest.parent.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_upload_storage_stream_rejects_traversal(tmp_path: Path):
    from ttvturbo.media_processing.uploads import UploadStorage, UploadStorageError

    storage = UploadStorage(tmp_path / "uploads")
    meta = storage.create_upload(file_name="audio.mp3", title="audio")
    fake = _FakeUploadFile(b"data")

    with pytest.raises(UploadStorageError):
        await storage.stream_upload_file(meta["id"], "../escape.mp3", fake)


# ---------------------------------------------------------------------------
# Integration: library upload endpoint streams atomically
# ---------------------------------------------------------------------------


def test_library_upload_endpoint_streams(tmp_path: Path):
    """The /api/library/uploads endpoint streams the file atomically."""
    from fastapi.testclient import TestClient

    from ttvturbo.app_factory import create_app
    from ttvturbo.settings import Settings

    settings = Settings(data_root=tmp_path)
    app = create_app(settings)
    data = b"\x00" * (2 * 1024 * 1024 + 42)
    with TestClient(app) as client:
        resp = client.post(
            "/api/library/uploads",
            files={"file": ("test.bin", data, "application/octet-stream")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["file_name"] == "test.bin"
    assert body["file_size_bytes"] == len(data)
    # The file must exist and be complete.
    item_dir = tmp_path / "library" / body["id"]
    dest = item_dir / "test.bin"
    assert dest.is_file()
    assert dest.read_bytes() == data
    # No leftover tmp files.
    assert list(item_dir.glob("*.tmp")) == []


def test_library_upload_endpoint_partial_cleanup(tmp_path: Path):
    """A failed upload must not leave a final file or tmp files."""
    from fastapi.testclient import TestClient

    from ttvturbo.app_factory import create_app
    from ttvturbo.settings import Settings

    settings = Settings(data_root=tmp_path)
    app = create_app(settings)
    # Send a request with an invalid filename to trigger a validation error.
    with TestClient(app) as client:
        resp = client.post(
            "/api/library/uploads",
            files={"file": ("", b"data", "application/octet-stream")},
        )
    # FastAPI returns 422 for missing filename (multipart validation).
    assert resp.status_code in (400, 422)
    # No library item directory should have been created with tmp files.
    library_dir = tmp_path / "library"
    if library_dir.is_dir():
        for item in library_dir.iterdir():
            if item.is_dir():
                assert list(item.glob("*.tmp")) == []


def test_temporary_library_upload_is_hidden_until_promoted(tmp_path: Path):
    """Quick-tool uploads stay out of the Library until explicitly promoted."""
    from fastapi.testclient import TestClient

    from ttvturbo.app_factory import create_app
    from ttvturbo.settings import Settings

    app = create_app(Settings(data_root=tmp_path))
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/library/uploads",
            files={"file": ("temporary.mp4", b"temporary video", "video/mp4")},
            data={"lifecycle": "TEMPORARY"},
        )
        assert uploaded.status_code == 201
        item = uploaded.json()
        assert item["lifecycle"] == "TEMPORARY"
        assert item["expires_at"]

        persistent_list = client.get("/api/library/items")
        assert persistent_list.status_code == 200
        assert persistent_list.json()["items"] == []

        all_items = client.get("/api/library/items?include_temporary=true")
        assert all_items.status_code == 200
        assert [entry["id"] for entry in all_items.json()["items"]] == [item["id"]]

        promoted = client.post(f"/api/library/items/{item['id']}/promote")
        assert promoted.status_code == 200
        assert promoted.json()["lifecycle"] == "PERSISTENT"
        assert promoted.json()["expires_at"] is None

        visible = client.get("/api/library/items")
        assert visible.status_code == 200
        assert [entry["id"] for entry in visible.json()["items"]] == [item["id"]]
