"""File-upload storage for independent transcription.

Stores uploaded media files in a dedicated directory tree so that
transcription can run without depending on the VOD downloader::

    {uploads_dir}/
      {upload_id}/
        metadata.json     # title, file_name, duration_seconds, ...
        source.{ext}      # the uploaded file
        artifacts/        # audio + transcripts (same layout as VOD dirs)

This mirrors the VOD directory layout (``vods/{vod_id}/``) so that
:class:`MediaSourceResolver`, :class:`AudioExtractionService` and
:class:`TranscriptionService` can treat uploads uniformly via
``source_type = "file_upload"``.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any, Optional

from .schemas import MediaSourceError, MediaSourceNotFoundError


class UploadStorageError(Exception):
    """Generic upload storage error."""


class UploadNotFoundError(MediaSourceNotFoundError):
    """Raised when an upload id does not exist."""


class UploadTooLargeError(UploadStorageError):
    """Raised when a streamed upload exceeds the configured byte limit."""


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


class UploadStorage:
    """File-based storage for uploaded media files."""

    def __init__(self, uploads_dir: Path) -> None:
        self.uploads_dir = Path(uploads_dir)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def _upload_dir(self, upload_id: str) -> Path:
        """Return the upload directory after UUID validation."""
        try:
            uid = uuid.UUID(upload_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise UploadNotFoundError(f"invalid upload id: {upload_id!r}") from exc
        return self.uploads_dir / str(uid)

    def _metadata_path(self, upload_id: str) -> Path:
        return self._upload_dir(upload_id) / "metadata.json"

    def create_upload(
        self,
        file_name: str,
        title: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> dict:
        """Create a new upload record and return its metadata.

        The caller is responsible for writing the actual source file into
        the upload directory (typically via ``upload_dir / file_name``).
        """
        upload_id = str(uuid.uuid4())
        upload_dir = self._upload_dir(upload_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        now = _now_iso()
        meta = {
            "schema_version": 1,
            "id": upload_id,
            "source_type": "file_upload",
            "title": title or file_name,
            "file_name": file_name,
            "duration_seconds": duration_seconds,
            "status": "READY",  # uploads are immediately usable
            "created_at": now,
            "updated_at": now,
        }
        with open(self._metadata_path(upload_id), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)
        return meta

    def load_upload(self, upload_id: str) -> dict:
        path = self._metadata_path(upload_id)
        if not path.is_file():
            raise UploadNotFoundError(f"upload not found: {upload_id}")
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise UploadStorageError(f"corrupt upload metadata: {exc}") from exc

    def list_uploads(self) -> list[dict]:
        """Return metadata for all uploads, sorted by creation date (newest first)."""
        results: list[dict] = []
        if not self.uploads_dir.is_dir():
            return results
        for entry in self.uploads_dir.iterdir():
            if not entry.is_dir():
                continue
            meta_path = entry / "metadata.json"
            if not meta_path.is_file():
                continue
            try:
                with open(meta_path, "r", encoding="utf-8-sig") as fh:
                    meta = json.load(fh)
                # Enrich with file size if the source file exists.
                src = entry / meta.get("file_name", "")
                if src.is_file():
                    meta["file_size_bytes"] = src.stat().st_size
                else:
                    meta["file_size_bytes"] = None
                results.append(meta)
            except (OSError, json.JSONDecodeError):
                continue
        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    def upload_dir(self, upload_id: str) -> Path:
        """Return the upload directory after validation."""
        return self._upload_dir(upload_id)

    def source_file_path(self, upload_id: str) -> Path:
        meta = self.load_upload(upload_id)
        return self._upload_dir(upload_id) / meta["file_name"]

    async def stream_upload_file(
        self,
        upload_id: str,
        file_name: str,
        file,
        *,
        chunk_size: int = 1024 * 1024,
        max_bytes: Optional[int] = None,
    ) -> Path:
        """Stream an uploaded file into the upload directory atomically.

        The file is written to a temporary path first, then atomically
        renamed to ``{upload_dir}/{file_name}`` via ``os.replace``.  A
        partial upload (client disconnect, network error) never leaves a
        half-written file at the final path — the temp file is cleaned up.

        When *max_bytes* is set, bytes are counted per chunk and the
        upload is aborted the moment the limit is exceeded: the temp file
        is deleted and :class:`UploadTooLargeError` is raised (callers
        map this to HTTP 413).

        *file* must be a Starlette/FastAPI ``UploadFile`` (or any object
        with an async ``read(n)`` method).  The caller is responsible for
        closing the upstream source.
        """
        import os

        from ttvturbo.storage_utils import atomic_tmp_name

        upload_dir = self._upload_dir(upload_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / Path(file_name).name
        if dest.name != file_name:
            raise UploadStorageError(f"invalid file_name: {file_name!r}")
        tmp_path = upload_dir / atomic_tmp_name(dest)
        received = 0
        try:
            with open(tmp_path, "wb") as fh:
                while True:
                    chunk = await file.read(chunk_size)
                    if not chunk:
                        break
                    received += len(chunk)
                    if max_bytes is not None and received > max_bytes:
                        raise UploadTooLargeError(
                            f"upload exceeds max_upload_bytes ({max_bytes})"
                        )
                    fh.write(chunk)
                    fh.flush()
                    os.fsync(fh.fileno())
            os.replace(tmp_path, dest)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        return dest

    def delete_upload(self, upload_id: str) -> bool:
        try:
            upload_dir = self._upload_dir(upload_id)
        except UploadNotFoundError:
            return False
        if not upload_dir.is_dir():
            return False
        import shutil

        shutil.rmtree(upload_dir, ignore_errors=True)
        return True
