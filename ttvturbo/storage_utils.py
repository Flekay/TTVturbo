"""Canonical storage primitives shared across all persistence modules.

This module is the **single implementation** of the atomic-write, read,
UUID-validation and timestamp helpers that were previously duplicated in
``vod_pipeline/storage.py``, ``library/storage.py``,
``media_processing/storage.py`` and ``media_processing/asr_benchmark.py``.

Design rules
------------
* **Atomic writes**: every write goes to a unique ``.tmp`` file
  (``.{name}.{pid}.{ns}.tmp``) then ``os.replace``.  The unique name
  eliminates the race where two writers (service process + worker
  subprocess) share a fixed ``.tmp`` name and one ``os.replace`` deletes
  the other's file.  On Windows, ``os.replace`` can briefly fail with
  ``PermissionError`` if another process holds the target open; the
  helper retries a few times before giving up.
* **Retry-on-read**: ``read_json`` retries on ``PermissionError`` for the
  same Windows-lock reason.
* **UUID validation**: rejects non-canonical UUIDs to prevent
  path-traversal via ``..`` or absolute paths.
* **No side effects on import**: this module only defines functions.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Current timestamp as ISO-8601 with local timezone, no microseconds."""
    return (
        _dt.datetime.now(tz=_dt.timezone.utc)
        .astimezone()
        .replace(microsecond=0)
        .isoformat()
    )


# ---------------------------------------------------------------------------
# UUID validation
# ---------------------------------------------------------------------------


def validate_uuid(value: str, kind: str, error_type: type[Exception]) -> str:
    """Reject anything that is not a canonical UUID string.

    Parameters
    ----------
    value:
        The string to validate.
    kind:
        Human-readable label for the error message (e.g. ``"vod"``).
    error_type:
        The exception class to raise on invalid input.  Different storage
        modules use different exception types (``VodStorageError``,
        ``LibraryStorageError``, …) so the caller picks the right one.
    """
    if not isinstance(value, str) or not value:
        raise error_type(f"{kind} id must be a non-empty string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise error_type(f"invalid {kind} id: {value!r}") from exc
    canonical = str(parsed)
    if canonical != value:
        raise error_type(f"{kind} id must be canonical uuid form: {value!r}")
    return value


# ---------------------------------------------------------------------------
# Path-traversal-safe directory resolution
# ---------------------------------------------------------------------------


def safe_record_dir(
    root: Path,
    record_id: str,
    kind: str,
    error_type: type[Exception],
) -> Path:
    """Resolve ``root / record_id`` with path-traversal protection.

    Validates *record_id* as a canonical UUID and ensures the resolved
    path stays inside *root*.  Returns the resolved directory path.
    """
    validate_uuid(record_id, kind, error_type)
    base = root.resolve()
    candidate = (root / record_id).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise error_type(
            f"{kind} id escapes storage root: {record_id!r}"
        ) from exc
    return candidate


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------


_WRITE_RETRIES = 5
_READ_RETRIES = 5
_RETRY_BACKOFF = 0.05  # seconds, multiplied by (attempt + 1)

# Reserved prefix/suffix for atomic-transaction temp files.
#
# Atomic JSON writes use the pattern::
#
#     .{target_name}.{pid}.{monotonic_ns}.tmp
#
# (optionally with a thread id between pid and ns for worker threads).
# These files are **transaction files**: they exist only for the few
# milliseconds between ``open()`` and ``os.replace()``.  Cleanup routines
# that remove download/worker partials must NEVER touch them, otherwise an
# active transaction can be deleted between write and replace, breaking
# cancel/retry/recovery.
#
# Download/worker partials use distinct, separate suffixes (``.part`` for
# binary download chunks, ``.dl_`` prefix for yt-dlp fragments) so cleanup
# can target them unambiguously.
ATOMIC_TMP_PREFIX = "."
ATOMIC_TMP_SUFFIX = ".tmp"

# Stale atomic temp files older than this are safe to remove: no real
# transaction takes this long, so a leftover file means a writer died
# before ``os.replace()``.  Kept generous so a slow disk under heavy load
# never trips it.
STALE_ATOMIC_TMP_MAX_AGE_SECONDS = 3600.0


def atomic_tmp_name(path: Path) -> str:
    """Return the unique temp file name for an atomic write to *path*.

    The name follows the reserved pattern
    ``.{path.name}.{pid}.{thread}.{monotonic_ns}.tmp`` so concurrent
    writers (service process + worker subprocess, or multiple worker
    threads within the same process) never collide on the same temp
    file.  ``threading.get_ident()`` disambiguates threads that happen
    to observe the same ``monotonic_ns()`` value.
    """
    import threading  # noqa: PLC0415

    return (
        f"{ATOMIC_TMP_PREFIX}{path.name}.{os.getpid()}"
        f".{threading.get_ident()}.{time.monotonic_ns()}{ATOMIC_TMP_SUFFIX}"
    )


def is_atomic_tmp(name: str) -> bool:
    """True if *name* matches the reserved atomic-transaction temp pattern.

    Recognises both the service form
    ``.{target}.{pid}.{ns}.tmp`` and the worker-thread form
    ``.{target}.{pid}.{thread}.{ns}.tmp``.  Used by cleanup routines to
    avoid deleting an active atomic transaction.
    """
    if not name.startswith(ATOMIC_TMP_PREFIX):
        return False
    if not name.endswith(ATOMIC_TMP_SUFFIX):
        return False
    # Must contain at least pid + ns segments between the leading dot and
    # the .tmp suffix.  e.g. ".metadata.json.1234.56789.tmp"
    core = name[len(ATOMIC_TMP_PREFIX):-len(ATOMIC_TMP_SUFFIX)]
    if not core:
        return False
    # The core is "{target}.{pid}[.{thread}].{ns}"; require at least two
    # dot-separated numeric tail segments (pid and ns).
    parts = core.split(".")
    if len(parts) < 3:
        return False
    # The last segment (ns) and the second-to-last (pid, or thread when a
    # pid precedes it) must be numeric.  We check the final two segments
    # are numeric; the target name itself may contain dots.
    if not (parts[-1].isdigit() and parts[-2].isdigit()):
        return False
    return True


def cleanup_stale_atomic_tmp(directory: Path, max_age_seconds: float = STALE_ATOMIC_TMP_MAX_AGE_SECONDS) -> int:
    """Remove atomic-transaction temp files older than *max_age_seconds*.

    Only files matching :func:`is_atomic_tmp` are considered, so download
    partials (``.part``, ``.dl_*``) and active transactions (younger than
    the threshold) are never touched.  Returns the number of files removed.

    Age is measured against the file's wall-clock mtime (``stat().st_mtime``)
    so the threshold is meaningful across process restarts.
    """
    if not directory.is_dir():
        return 0
    now = time.time()
    removed = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_file():
            continue
        if not is_atomic_tmp(entry.name):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if (now - mtime) < max_age_seconds:
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def atomic_write_json(
    path: Path,
    payload: dict,
    error_type: type[Exception],
    *,
    kind: str = "record",
) -> None:
    """Write *payload* to *path* atomically via a unique ``.tmp`` file.

    The tmp file is named ``.{path.name}.{pid}.{monotonic_ns}.tmp`` so
    concurrent writers (service + worker subprocess) never collide.
    Retries ``PermissionError`` on Windows where ``os.replace`` briefly
    holds an exclusive lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(atomic_tmp_name(path))
    last_exc: Optional[Exception] = None
    for attempt in range(_WRITE_RETRIES):
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
        except OSError as exc:
            _cleanup_tmp(tmp)
            raise error_type(f"could not write {kind} {path}: {exc}") from exc
    _cleanup_tmp(tmp)
    raise error_type(f"could not write {kind} {path}: {last_exc}") from last_exc


def _cleanup_tmp(tmp: Path) -> None:
    """Best-effort unlink of a tmp file."""
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# JSON read with retry
# ---------------------------------------------------------------------------


def read_json(path: Path, error_type: type[Exception], *, kind: str = "record") -> dict:
    """Read and parse a JSON file with Windows-lock retry.

    Raises *error_type* on corrupt/missing files.  Uses ``utf-8-sig`` to
    transparently strip a UTF-8 BOM if present.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(_READ_RETRIES):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
            break
        except PermissionError as exc:
            last_exc = exc
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
        except (OSError, json.JSONDecodeError) as exc:
            raise error_type(f"corrupt file {path}: {exc}") from exc
    else:
        raise error_type(f"corrupt file {path}: {last_exc}") from last_exc
    if not isinstance(payload, dict):
        raise error_type(f"{path} is not a JSON object")
    return payload


def read_json_optional(path: Path) -> Optional[dict]:
    """Read a JSON file, returning ``None`` if missing or corrupt.

    Unlike :func:`read_json`, this never raises — it logs nothing and
    returns ``None`` for any read/parse failure.  Used by listing
    operations that skip bad records.
    """
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError, PermissionError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload
