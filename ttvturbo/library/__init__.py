"""TTVturbo library — the persistent, independent video store.

The library owns downloaded VOD files and uploaded media files. It is
deliberately decoupled from the VOD pipeline (which is ephemeral: sync
state, download progress, worker logs). See :mod:`library.schemas` for
the on-disk layout and design rationale.
"""

from __future__ import annotations

from .schemas import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
    LibraryStorageError,
    LibraryUploadTooLargeError,
    LibraryValidationError,
)
from .storage import LibraryStorage
from .service import LibraryService

__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "LibraryConflictError",
    "LibraryError",
    "LibraryNotFoundError",
    "LibraryService",
    "LibraryStorage",
    "LibraryStorageError",
    "LibraryUploadTooLargeError",
    "LibraryValidationError",
]
