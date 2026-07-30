"""TTVturbo library — the persistent, independent video store.

The library owns downloaded VOD files and uploaded media files. It is
deliberately decoupled from the VOD pipeline (which is ephemeral: sync
state, download progress, worker logs). See :mod:`library.schemas` for
the on-disk layout and design rationale.
"""

from __future__ import annotations

from .schemas import (
    LIFECYCLE_PERSISTENT,
    LIFECYCLE_TEMPORARY,
    SUPPORTED_LIFECYCLES,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    FILE_TYPE_VIDEO,
    FILE_TYPE_AUDIO,
    FILE_TYPE_IMAGE,
    SUPPORTED_FILE_TYPES,
    EXTENSIONS_BY_FILE_TYPE,
    file_type_for_extension,
    file_type_for_filename,
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
    "LIFECYCLE_PERSISTENT",
    "LIFECYCLE_TEMPORARY",
    "SUPPORTED_LIFECYCLES",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "FILE_TYPE_VIDEO",
    "FILE_TYPE_AUDIO",
    "FILE_TYPE_IMAGE",
    "SUPPORTED_FILE_TYPES",
    "EXTENSIONS_BY_FILE_TYPE",
    "file_type_for_extension",
    "file_type_for_filename",
    "LibraryConflictError",
    "LibraryError",
    "LibraryNotFoundError",
    "LibraryService",
    "LibraryStorage",
    "LibraryStorageError",
    "LibraryUploadTooLargeError",
    "LibraryValidationError",
]
