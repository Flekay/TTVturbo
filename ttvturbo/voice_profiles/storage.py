"""Atomar, file-based persistence for voice profiles.

Layout::

    {root_dir}/
        {profile_id}/
            profile.json        <- committed
            profile.json.tmp    <- transient, never read back as a profile

Writes are atomic: ``profile.json.tmp`` -> ``flush`` -> ``os.replace`` ->
``profile.json``. No half-written file is ever observable as a profile.

Safety guarantees:

* profile ids must be valid UUIDs (path-traversal protection);
* the resolved profile directory must stay inside ``root_dir``;
* corrupt JSON files are logged and skipped during listing, never raised;
* an unknown ``schema_version`` is rejected (not silently interpreted);
* ``*.tmp`` files are never treated as profiles.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Iterator, Optional

from .schemas import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    VoiceProfileError,
    VoiceProfileNotFoundError,
    VoiceProfileStorageError,
)

logger = logging.getLogger("ttvturbo.voice_profiles.storage")

PROFILE_FILENAME = "profile.json"
PROFILE_TMP_SUFFIX = ".tmp"


class VoiceProfileStorage:
    """Filesystem-backed profile store with atomic writes."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _validate_profile_id(profile_id: str) -> str:
        """Reject anything that is not a canonical UUID string."""
        if not isinstance(profile_id, str) or not profile_id:
            raise VoiceProfileStorageError("profile id must be a non-empty string")
        try:
            parsed = uuid.UUID(profile_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise VoiceProfileStorageError(f"invalid profile id: {profile_id!r}") from exc
        # Require canonical form to avoid surprising on-disk paths.
        canonical = str(parsed)
        if canonical != profile_id:
            raise VoiceProfileStorageError(
                f"profile id must be canonical uuid form: {profile_id!r}"
            )
        return profile_id

    def _profile_dir(self, profile_id: str) -> Path:
        pid = self._validate_profile_id(profile_id)
        base = self.root_dir.resolve()
        candidate = (self.root_dir / pid).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise VoiceProfileStorageError(
                f"profile id escapes storage root: {profile_id!r}"
            ) from exc
        return candidate

    def _profile_path(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / PROFILE_FILENAME

    # ------------------------------------------------------------------ write
    def save_profile(self, payload: dict) -> None:
        """Atomically write a profile dict to disk.

        The profile id is taken from ``payload["id"]``. The dict must declare
        a supported ``schema_version``.
        """
        if not isinstance(payload, dict):
            raise VoiceProfileStorageError("payload must be a dict")
        profile_id = payload.get("id")
        schema_version = payload.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise VoiceProfileStorageError(
                f"unsupported schema_version {schema_version!r}"
            )
        if profile_id is None:
            raise VoiceProfileStorageError("payload missing id")
        path = self._profile_path(str(profile_id))
        tmp = path.with_name(path.name + PROFILE_TMP_SUFFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # fsync may be unavailable on some platforms / streams;
                    # the flush + atomic replace still gives crash-consistency
                    # for the common case. Do not raise.
                    pass
            os.replace(tmp, path)
        except OSError as exc:
            # Best-effort cleanup of the tmp file on failure.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise VoiceProfileStorageError(f"could not write profile {profile_id}: {exc}") from exc

    # ------------------------------------------------------------------ read
    def load_profile(self, profile_id: str) -> dict:
        """Load a single profile by id.

        Raises :class:`VoiceProfileNotFoundError` if the profile does not
        exist and :class:`VoiceProfileStorageError` if it is corrupt, has
        an unknown schema version, or has an invalid id.
        """
        pid = self._validate_profile_id(profile_id)
        path = self._profile_path(pid)
        if not path.is_file():
            raise VoiceProfileNotFoundError(f"profile not found: {pid}")
        return self._read_profile_file(path)

    def _read_profile_file(self, path: Path) -> dict:
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise VoiceProfileStorageError(f"corrupt profile {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise VoiceProfileStorageError(f"profile {path} is not a JSON object")
        schema_version = payload.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise VoiceProfileStorageError(
                f"unknown schema_version {schema_version!r} in {path}"
            )
        return payload

    # ------------------------------------------------------------------ list
    def iter_profiles(self) -> Iterator[dict]:
        """Yield every readable profile dict on disk.

        Corrupt JSON files, unknown schema versions and stray ``*.tmp``
        files are logged and skipped. They never stop the iteration.
        """
        try:
            entries = list(self.root_dir.iterdir())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not scan profile root %s: %s", self.root_dir, exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            # Skip non-UUID directory names silently: they are not profiles.
            try:
                self._validate_profile_id(entry.name)
            except VoiceProfileStorageError:
                continue
            profile_path = entry / PROFILE_FILENAME
            if not profile_path.is_file():
                continue
            try:
                payload = self._read_profile_file(profile_path)
            except VoiceProfileStorageError as exc:
                logger.warning("Skipping unreadable profile %s: %s", profile_path, exc)
                continue
            yield payload

    # ------------------------------------------------------------------ delete
    def delete_profile(self, profile_id: str) -> bool:
        """Remove the profile directory. Returns True if something was removed.

        Never touches anything outside ``{root}/{profile_id}``.
        """
        pid = self._validate_profile_id(profile_id)
        profile_dir = self._profile_dir(pid)
        if not profile_dir.exists():
            return False
        # Atomic-ish: rename then rmtree. Avoids half-deleted observable state.
        tmp = profile_dir.with_name(profile_dir.name + ".deleting")
        try:
            os.replace(profile_dir, tmp)
        except OSError as exc:
            raise VoiceProfileStorageError(f"could not delete profile {pid}: {exc}") from exc
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ misc
    def profile_exists(self, profile_id: str) -> bool:
        pid = self._validate_profile_id(profile_id)
        return self._profile_path(pid).is_file()
