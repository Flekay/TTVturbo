"""Atomicar, file-based persistence for the VOD pipeline.

Layout::

    {root_dir}/
        twitch_profiles/
            {profile_id}/
                profile.json        <- committed
                profile.json.tmp    <- transient, never read back
        vods/
            {vod_id}/
                metadata.json       <- committed
                metadata.json.tmp   <- transient
                worker.log
                source.<container>

Writes are atomic: ``*.tmp`` -> ``flush`` -> ``os.replace`` -> committed
file. No half-written file is ever observable as a record.

Safety guarantees mirror :mod:`voice_profiles.storage`:

* ids must be valid canonical UUIDs (path-traversal protection);
* the resolved record directory must stay inside its root;
* corrupt JSON files are logged and skipped during listing, never raised;
* an unknown ``schema_version`` is rejected (not silently interpreted);
* ``*.tmp`` / ``*.deleting`` files are never treated as records.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Iterator, Optional

from ttvturbo.storage_utils import (
    atomic_write_json,
    read_json,
    safe_record_dir,
    validate_uuid,
)

from .schemas import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TwitchProfileNotFoundError,
    TwitchProfileStorageError,
    VodNotFoundError,
    VodStorageError,
)

logger = logging.getLogger("ttvturbo.vod_pipeline.storage")

PROFILE_FILENAME = "profile.json"
VOD_FILENAME = "metadata.json"
TMP_SUFFIX = ".tmp"
WORKER_LOG_NAME = "worker.log"


def _validate_uuid(value: str, kind: str) -> str:
    """Reject anything that is not a canonical UUID string (delegates to storage_utils)."""
    error_type = TwitchProfileStorageError if kind == "profile" else VodStorageError
    return validate_uuid(value, kind, error_type)


class VodPipelineStorage:
    """Filesystem-backed store for Twitch profiles and VOD records."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "twitch_profiles"
        self.vods_dir = self.data_dir / "vods"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.vods_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _profile_dir(self, profile_id: str) -> Path:
        return safe_record_dir(self.profiles_dir, profile_id, "profile", TwitchProfileStorageError)

    def _profile_path(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / PROFILE_FILENAME

    def _vod_dir(self, vod_id: str) -> Path:
        return safe_record_dir(self.vods_dir, vod_id, "vod", VodStorageError)

    def vod_dir(self, vod_id: str) -> Path:
        """Public accessor for the VOD directory (UUID-validated, traversal-safe).

        External callers (source resolver, tests) should use this instead
        of the private ``_vod_dir``.
        """
        return self._vod_dir(vod_id)

    def _vod_path(self, vod_id: str) -> Path:
        return self._vod_dir(vod_id) / VOD_FILENAME

    def vod_worker_log_path(self, vod_id: str) -> Path:
        return self._vod_dir(vod_id) / WORKER_LOG_NAME

    # ------------------------------------------------------------------ write
    def _atomic_write_json(self, path: Path, payload: dict) -> None:
        kind = "profile" if path.name == PROFILE_FILENAME else "vod"
        error_type = TwitchProfileStorageError if kind == "profile" else VodStorageError
        atomic_write_json(path, payload, error_type, kind=kind)

    def save_profile(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise TwitchProfileStorageError("payload must be a dict")
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise TwitchProfileStorageError(
                f"unsupported profile schema_version {payload.get('schema_version')!r}"
            )
        if not payload.get("id"):
            raise TwitchProfileStorageError("payload missing id")
        self._atomic_write_json(self._profile_path(str(payload["id"])), payload)

    def save_vod(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise VodStorageError("payload must be a dict")
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise VodStorageError(
                f"unsupported vod schema_version {payload.get('schema_version')!r}"
            )
        if not payload.get("id"):
            raise VodStorageError("payload missing id")
        self._atomic_write_json(self._vod_path(str(payload["id"])), payload)

    # ------------------------------------------------------------------ read
    def _read_json(self, path: Path, error_type: type[Exception]) -> dict:
        kind = "profile" if path.name == PROFILE_FILENAME else "vod"
        return read_json(path, error_type, kind=kind)

    def load_profile(self, profile_id: str) -> dict:
        path = self._profile_path(profile_id)
        if not path.is_file():
            raise TwitchProfileNotFoundError(f"profile not found: {profile_id}")
        payload = self._read_json(path, TwitchProfileStorageError)
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise TwitchProfileStorageError(
                f"unknown profile schema_version {payload.get('schema_version')!r}"
            )
        return payload

    def load_vod(self, vod_id: str) -> dict:
        path = self._vod_path(vod_id)
        if not path.is_file():
            raise VodNotFoundError(f"vod not found: {vod_id}")
        payload = self._read_json(path, VodStorageError)
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise VodStorageError(
                f"unknown vod schema_version {payload.get('schema_version')!r}"
            )
        return payload

    # ------------------------------------------------------------------ list
    def iter_profiles(self) -> Iterator[dict]:
        try:
            entries = list(self.profiles_dir.iterdir())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not scan profiles root %s: %s", self.profiles_dir, exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                _validate_uuid(entry.name, "profile")
            except TwitchProfileStorageError:
                continue
            path = entry / PROFILE_FILENAME
            if not path.is_file():
                continue
            try:
                payload = self._read_json(path, TwitchProfileStorageError)
            except TwitchProfileStorageError as exc:
                logger.warning("Skipping unreadable profile %s: %s", path, exc)
                continue
            if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                logger.warning("Skipping profile %s: unknown schema_version", path)
                continue
            yield payload

    def iter_vods(self) -> Iterator[dict]:
        try:
            entries = list(self.vods_dir.iterdir())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not scan vods root %s: %s", self.vods_dir, exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                _validate_uuid(entry.name, "vod")
            except VodStorageError:
                continue
            path = entry / VOD_FILENAME
            if not path.is_file():
                continue
            try:
                payload = self._read_json(path, VodStorageError)
            except VodStorageError as exc:
                logger.warning("Skipping unreadable vod %s: %s", path, exc)
                continue
            if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                logger.warning("Skipping vod %s: unknown schema_version", path)
                continue
            yield payload

    # ------------------------------------------------------------------ find
    def find_profile_by_twitch_user_id(self, twitch_user_id: str) -> Optional[dict]:
        for p in self.iter_profiles():
            if p.get("twitch_user_id") == str(twitch_user_id):
                return p
        return None

    def find_vod_by_twitch_video_id(self, twitch_video_id: str) -> Optional[dict]:
        for v in self.iter_vods():
            if v.get("twitch_video_id") == str(twitch_video_id):
                return v
        return None

    def find_vods_for_profile(self, profile_id: str) -> list[dict]:
        return [v for v in self.iter_vods() if v.get("profile_id") == profile_id]

    # ------------------------------------------------------------------ delete
    def delete_profile(self, profile_id: str) -> bool:
        profile_dir = self._profile_dir(profile_id)
        if not profile_dir.exists():
            return False
        tmp = profile_dir.with_name(profile_dir.name + ".deleting")
        try:
            os.replace(profile_dir, tmp)
        except OSError as exc:
            raise TwitchProfileStorageError(
                f"could not delete profile {profile_id}: {exc}"
            ) from exc
        shutil.rmtree(tmp, ignore_errors=True)
        return True

    def delete_vod(self, vod_id: str) -> bool:
        vod_dir = self._vod_dir(vod_id)
        if not vod_dir.exists():
            return False
        tmp = vod_dir.with_name(vod_dir.name + ".deleting")
        try:
            os.replace(vod_dir, tmp)
        except OSError as exc:
            raise VodStorageError(f"could not delete vod {vod_id}: {exc}") from exc
        shutil.rmtree(tmp, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ misc
    def profile_exists(self, profile_id: str) -> bool:
        return self._profile_path(profile_id).is_file()

    def vod_exists(self, vod_id: str) -> bool:
        return self._vod_path(vod_id).is_file()
