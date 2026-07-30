"""Schemas for the library — the persistent, independent media store.

The library is a standalone system that owns downloaded VOD files and
uploaded media files. It is deliberately decoupled from the VOD pipeline:

* VODs are ephemeral session records (sync state, download progress,
  worker logs). Their downloaded video files are promoted into the
  library on completion.
* Uploads are created directly as library items.
* Deleting a Twitch profile deletes the VOD metadata + temp files but
  leaves library items intact. The ``vod_id`` back-reference is nulled.
* Duplication check: before downloading a VOD, the pipeline checks
  ``twitch_video_id`` in the library. If an item already exists, the
  VOD references it instead of re-downloading.

The library is *file-aware* (schema v2): every item carries a
``file_type`` (``video`` | ``audio`` | ``image``) so the API and UI can
sort and filter by media type, and non-video uploads (images, audio) are
stored with their real extension instead of being forced to ``mp4``.

Layout::

    {library_dir}/
        {item_id}/
            metadata.json       <- committed
            source.<ext>        <- the media file (keeps its real extension)
            artifacts/          <- audio + transcripts (uploads only)

Metadata schema (v2)::

    {
      "schema_version": 2,
      "id": "<uuid>",
      "source": "vod" | "upload",
      "title": "<str>",
      "file_name": "source.mp4",          # canonical filename on disk
      "file_size_bytes": 1234567,         # enriched by list_items
      "duration_seconds": 3600.0,         # from ffprobe (vod) or probe (upload)
      "file_type": "video",               # video | audio | image
      "container": "mp4",                 # file extension (kept for compat)
      "twitch_video_id": "1234567890",    # nullable, for VOD dedup
      "vod_id": "<uuid>",                 # nullable, back-reference to VOD
      "created_at": "<iso>",
      "updated_at": "<iso>"
    }
"""

from __future__ import annotations

SCHEMA_VERSION = 2
# v1 items are still readable (file_type is derived lazily), but every
# newly written item is v2. The one-time migration script upgrades
# existing v1 items in place.
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})

LIFECYCLE_TEMPORARY = "TEMPORARY"
LIFECYCLE_PERSISTENT = "PERSISTENT"
SUPPORTED_LIFECYCLES = frozenset({LIFECYCLE_TEMPORARY, LIFECYCLE_PERSISTENT})

# --------------------------------------------------------------------- file types
FILE_TYPE_VIDEO = "video"
FILE_TYPE_AUDIO = "audio"
FILE_TYPE_IMAGE = "image"
SUPPORTED_FILE_TYPES = frozenset({FILE_TYPE_VIDEO, FILE_TYPE_AUDIO, FILE_TYPE_IMAGE})

# Extension -> file_type mapping. Lower-case, no leading dot.
# Video containers are the historical ``SUPPORTED_CONTAINERS``; audio and
# image extensions were added with schema v2.
_VIDEO_EXTENSIONS = frozenset({"mp4", "mkv", "webm", "mov"})
_AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"})
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp", "svg"})

# All extensions the library accepts on upload, grouped by type.
EXTENSIONS_BY_FILE_TYPE: dict[str, frozenset[str]] = {
    FILE_TYPE_VIDEO: _VIDEO_EXTENSIONS,
    FILE_TYPE_AUDIO: _AUDIO_EXTENSIONS,
    FILE_TYPE_IMAGE: _IMAGE_EXTENSIONS,
}

# Flat lookup: extension -> file_type. Built once at import time.
_FILE_TYPE_BY_EXTENSION: dict[str, str] = {}
for _ft, _exts in EXTENSIONS_BY_FILE_TYPE.items():
    for _ext in _exts:
        _FILE_TYPE_BY_EXTENSION[_ext] = _ft
del _ft, _exts, _ext


def file_type_for_extension(extension: str) -> str | None:
    """Return the ``file_type`` for a given extension (lower-case, no dot).

    Returns ``None`` for unknown extensions. ``extension`` is normalised
    (stripped of a leading dot, lower-cased) before lookup.
    """
    ext = (extension or "").lstrip(".").lower()
    return _FILE_TYPE_BY_EXTENSION.get(ext)


def file_type_for_filename(file_name: str) -> str | None:
    """Return the ``file_type`` for a filename, based on its suffix."""
    if not file_name:
        return None
    return file_type_for_extension(file_name.rsplit(".", 1)[-1] if "." in file_name else "")


class LibraryError(Exception):
    """Base error for library operations."""


class LibraryNotFoundError(LibraryError):
    """Raised when a library item id does not exist."""


class LibraryStorageError(LibraryError):
    """Raised on storage-level failures (corrupt JSON, IO errors)."""


class LibraryUploadTooLargeError(LibraryStorageError):
    """Raised when a streamed upload exceeds the configured byte limit."""


class LibraryValidationError(LibraryError):
    """Raised on invalid input to library operations."""


class LibraryConflictError(LibraryError):
    """Raised when a twitch_video_id is already in the library."""
