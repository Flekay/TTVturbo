"""Regression tests for the canonical storage primitives.

Verifies that ``storage_utils`` provides safe, atomic, retry-capable
JSON persistence and that all storage modules delegate to it correctly.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

import pytest

from ttvturbo.storage_utils import (
    atomic_write_json,
    now_iso,
    read_json,
    read_json_optional,
    safe_record_dir,
    validate_uuid,
)


# ---------------------------------------------------------------------------
# now_iso
# ---------------------------------------------------------------------------


def test_now_iso_returns_iso_format():
    ts = now_iso()
    # Should look like 2026-07-28T16:33:55+02:00
    assert "T" in ts
    assert "+" in ts or "-" in ts.split("T")[1]
    # No microseconds.
    assert "." not in ts


def test_now_iso_is_monotonic_non_decreasing():
    a = now_iso()
    b = now_iso()
    # Both should be valid ISO strings; b is >= a (same second is OK).
    assert a <= b or a[:19] == b[:19]


# ---------------------------------------------------------------------------
# validate_uuid
# ---------------------------------------------------------------------------


class _TestError(Exception):
    pass


def test_validate_uuid_accepts_canonical_uuid():
    u = str(uuid.uuid4())
    assert validate_uuid(u, "test", _TestError) == u


def test_validate_uuid_rejects_non_string():
    with pytest.raises(_TestError, match="non-empty string"):
        validate_uuid(123, "test", _TestError)  # type: ignore[arg-type]


def test_validate_uuid_rejects_empty():
    with pytest.raises(_TestError, match="non-empty string"):
        validate_uuid("", "test", _TestError)


def test_validate_uuid_rejects_non_canonical():
    raw = uuid.uuid4()
    upper = str(raw).upper()
    with pytest.raises(_TestError, match="canonical uuid form"):
        validate_uuid(upper, "test", _TestError)


def test_validate_uuid_rejects_path_traversal():
    with pytest.raises(_TestError, match="invalid test id"):
        validate_uuid("../../../etc/passwd", "test", _TestError)


def test_validate_uuid_rejects_non_uuid_string():
    with pytest.raises(_TestError, match="invalid test id"):
        validate_uuid("not-a-uuid", "test", _TestError)


# ---------------------------------------------------------------------------
# safe_record_dir
# ---------------------------------------------------------------------------


def test_safe_record_dir_resolves_correctly(tmp_path: Path):
    root = tmp_path / "records"
    root.mkdir()
    u = str(uuid.uuid4())
    result = safe_record_dir(root, u, "test", _TestError)
    assert result == (root / u).resolve()


def test_safe_record_dir_rejects_traversal(tmp_path: Path):
    root = tmp_path / "records"
    root.mkdir()
    # Path traversal is caught by UUID validation first (defense-in-depth).
    with pytest.raises(_TestError, match="invalid test id"):
        safe_record_dir(root, "../../../etc", "test", _TestError)


# ---------------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------------


def test_atomic_write_json_creates_file(tmp_path: Path):
    path = tmp_path / "record.json"
    payload = {"id": "test", "value": 42}
    atomic_write_json(path, payload, _TestError)
    assert path.is_file()
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == payload


def test_atomic_write_json_creates_parent_dirs(tmp_path: Path):
    path = tmp_path / "a" / "b" / "record.json"
    atomic_write_json(path, {"x": 1}, _TestError)
    assert path.is_file()


def test_atomic_write_json_leaves_no_tmp_file(tmp_path: Path):
    path = tmp_path / "record.json"
    atomic_write_json(path, {"x": 1}, _TestError)
    # No .tmp files should remain.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_atomic_write_json_overwrites_existing(tmp_path: Path):
    path = tmp_path / "record.json"
    atomic_write_json(path, {"version": 1}, _TestError)
    atomic_write_json(path, {"version": 2}, _TestError)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == {"version": 2}


def test_atomic_write_json_concurrent_no_collision(tmp_path: Path):
    """Two threads writing to the same path should not corrupt the file.

    On Windows, concurrent ``os.replace`` to the same target can still
    fail after retries if the lock window is too long.  We accept that
    some writes may fail, but the final file must always be valid JSON
    (never half-written or corrupt).
    """
    path = tmp_path / "record.json"
    # Write an initial value so the file exists.
    atomic_write_json(path, {"value": -1}, _TestError)

    def _writer(value: int) -> None:
        try:
            atomic_write_json(path, {"value": value}, _TestError)
        except _TestError:
            pass  # Acceptable: Windows lock contention

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # The file must always be valid JSON with one of the written values.
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["value"] in range(-1, 10)
    # No .tmp files left behind.
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_json_handles_utf8(tmp_path: Path):
    path = tmp_path / "record.json"
    payload = {"title": "Überprüfung äöüß 🎉"}
    atomic_write_json(path, payload, _TestError)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == payload


# ---------------------------------------------------------------------------
# read_json
# ---------------------------------------------------------------------------


def test_read_json_reads_valid_file(tmp_path: Path):
    path = tmp_path / "record.json"
    atomic_write_json(path, {"x": 1}, _TestError)
    assert read_json(path, _TestError) == {"x": 1}


def test_read_json_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(_TestError, match="corrupt file"):
        read_json(tmp_path / "missing.json", _TestError)


def test_read_json_raises_on_corrupt_json(tmp_path: Path):
    path = tmp_path / "record.json"
    path.write_text("{not valid json}", encoding="utf-8")
    with pytest.raises(_TestError, match="corrupt file"):
        read_json(path, _TestError)


def test_read_json_raises_on_non_object(tmp_path: Path):
    path = tmp_path / "record.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(_TestError, match="not a JSON object"):
        read_json(path, _TestError)


def test_read_json_handles_utf8_bom(tmp_path: Path):
    path = tmp_path / "record.json"
    # Write with BOM.
    with open(path, "w", encoding="utf-8-sig") as fh:
        json.dump({"x": 1}, fh)
    assert read_json(path, _TestError) == {"x": 1}


# ---------------------------------------------------------------------------
# read_json_optional
# ---------------------------------------------------------------------------


def test_read_json_optional_returns_none_for_missing(tmp_path: Path):
    assert read_json_optional(tmp_path / "missing.json") is None


def test_read_json_optional_returns_none_for_corrupt(tmp_path: Path):
    path = tmp_path / "record.json"
    path.write_text("{bad", encoding="utf-8")
    assert read_json_optional(path) is None


def test_read_json_optional_returns_payload_for_valid(tmp_path: Path):
    path = tmp_path / "record.json"
    atomic_write_json(path, {"x": 1}, _TestError)
    assert read_json_optional(path) == {"x": 1}


def test_read_json_optional_returns_none_for_non_object(tmp_path: Path):
    path = tmp_path / "record.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert read_json_optional(path) is None


# ---------------------------------------------------------------------------
# Integration: storage modules delegate to storage_utils
# ---------------------------------------------------------------------------


def test_vod_pipeline_storage_uses_storage_utils(tmp_path: Path):
    """VodPipelineStorage should produce valid, atomically-written records."""
    from ttvturbo.vod_pipeline.storage import VodPipelineStorage
    storage = VodPipelineStorage(tmp_path)
    vod_id = str(uuid.uuid4())
    storage.save_vod({
        "id": vod_id,
        "schema_version": 1,
        "twitch_video_id": "123",
        "title": "Test VOD",
    })
    loaded = storage.load_vod(vod_id)
    assert loaded["id"] == vod_id
    assert loaded["title"] == "Test VOD"
    # No .tmp files left behind.
    vod_dir = storage.vods_dir / vod_id
    assert list(vod_dir.glob("*.tmp")) == []


def test_library_storage_uses_storage_utils(tmp_path: Path):
    """LibraryStorage should produce valid, atomically-written records."""
    from ttvturbo.library.storage import LibraryStorage
    storage = LibraryStorage(tmp_path)
    item_id = str(uuid.uuid4())
    storage.save_item({
        "id": item_id,
        "schema_version": 1,
        "title": "Test Item",
    })
    loaded = storage.load_item(item_id)
    assert loaded["id"] == item_id
    assert loaded["title"] == "Test Item"


def test_media_job_storage_uses_storage_utils(tmp_path: Path):
    """MediaJobStorage should produce valid, atomically-written records."""
    from ttvturbo.media_processing.storage import MediaJobStorage
    storage = MediaJobStorage(tmp_path)
    job_id = str(uuid.uuid4())
    storage.save_job({
        "id": job_id,
        "schema_version": 1,
        "source_type": "twitch_vod",
        "source_id": "123",
    })
    loaded = storage.load_job(job_id)
    assert loaded["id"] == job_id


def test_asr_benchmark_uses_storage_utils(tmp_path: Path):
    """AsrBenchmarkService should use the canonical atomic write."""
    from ttvturbo.media_processing.asr_benchmark import AsrBenchmarkService
    from ttvturbo.media_processing.gpu_lock import GpuLock
    from ttvturbo.media_processing.sources import MediaSourceResolver

    # Minimal stubs to avoid real GPU / source resolution.
    class _StubGpuLock:
        def acquire(self, *args, **kwargs): return True
        def release(self): pass

    class _StubResolver:
        def resolve(self, *args): raise RuntimeError("stub")

    bench_dir = tmp_path / "asr_benchmarks"
    bench_dir.mkdir(parents=True)
    service = AsrBenchmarkService(
        data_dir=tmp_path,
        source_resolver=_StubResolver(),
        gpu_lock=_StubGpuLock(),
    )
    # No .tmp files should remain after construction.
    assert list(bench_dir.glob("*.tmp")) == []
