"""Schemas for the library — the persistent, independent video store.

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

Layout::

    {library_dir}/
        {item_id}/
            metadata.json       <- committed
            source.<container>  <- the video file
            artifacts/          <- audio + transcripts (uploads only)

Metadata schema (v1)::

    {
      "schema_version": 1,
      "id": "<uuid>",
      "source": "vod" | "upload",
      "title": "<str>",
      "file_name": "source.mp4",          # canonical filename on disk
      "file_size_bytes": 1234567,         # enriched by list_items
      "duration_seconds": 3600.0,         # from ffprobe (vod) or probe (upload)
      "container": "mp4",                 # file extension
      "twitch_video_id": "1234567890",    # nullable, for VOD dedup
      "vod_id": "<uuid>",                 # nullable, back-reference to VOD
      "created_at": "<iso>",
      "updated_at": "<iso>"
    }
"""

from __future__ import annotations

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


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
