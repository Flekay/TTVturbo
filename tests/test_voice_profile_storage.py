"""Tests for :mod:`voice_profiles.storage`."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from voice_profiles.schemas import (
    SCHEMA_VERSION,
    VoiceProfileNotFoundError,
    VoiceProfileStorageError,
)
from voice_profiles.storage import PROFILE_FILENAME, VoiceProfileStorage


def _profile_dict(name: str = "Meine Stimme") -> dict:
    pid = str(uuid.uuid4())
    return {
        "schema_version": SCHEMA_VERSION,
        "id": pid,
        "name": name,
        "locale": "de-DE",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "references": {},
    }


@pytest.fixture()
def storage(tmp_path: Path) -> VoiceProfileStorage:
    return VoiceProfileStorage(tmp_path / "voice_profiles_data")


class TestSaveLoad:
    def test_save_and_load_roundtrip(self, storage: VoiceProfileStorage) -> None:
        payload = _profile_dict()
        storage.save_profile(payload)
        loaded = storage.load_profile(payload["id"])
        assert loaded == payload

    def test_load_missing_raises_typed(
        self, storage: VoiceProfileStorage
    ) -> None:
        with pytest.raises(VoiceProfileNotFoundError):
            storage.load_profile(str(uuid.uuid4()))


class TestAtomicWrite:
    def test_no_tmp_file_left_after_save(self, storage: VoiceProfileStorage) -> None:
        payload = _profile_dict()
        storage.save_profile(payload)
        profile_dir = storage.root_dir / payload["id"]
        files = sorted(p.name for p in profile_dir.iterdir())
        assert files == [PROFILE_FILENAME]
        assert not (profile_dir / (PROFILE_FILENAME + ".tmp")).exists()

    def test_atomic_replace_used(self, storage: VoiceProfileStorage) -> None:
        payload = _profile_dict()
        storage.save_profile(payload)
        # Overwrite with a new name; the on-disk file must reflect the new
        # content fully, never a partial write.
        payload["name"] = "Neuer Name"
        storage.save_profile(payload)
        loaded = storage.load_profile(payload["id"])
        assert loaded["name"] == "Neuer Name"


class TestCorruptJson:
    def test_corrupt_json_skipped_in_list(self, storage: VoiceProfileStorage) -> None:
        good = _profile_dict()
        storage.save_profile(good)
        # Write a corrupt profile alongside it.
        bad_id = str(uuid.uuid4())
        bad_dir = storage.root_dir / bad_id
        bad_dir.mkdir(parents=True)
        (bad_dir / PROFILE_FILENAME).write_text("{not valid json", encoding="utf-8")
        profiles = list(storage.iter_profiles())
        ids = {p["id"] for p in profiles}
        assert good["id"] in ids
        assert bad_id not in ids

    def test_corrupt_json_raises_on_direct_load(
        self, storage: VoiceProfileStorage
    ) -> None:
        bad_id = str(uuid.uuid4())
        bad_dir = storage.root_dir / bad_id
        bad_dir.mkdir(parents=True)
        (bad_dir / PROFILE_FILENAME).write_text("{not valid json", encoding="utf-8")
        with pytest.raises(VoiceProfileStorageError):
            storage.load_profile(bad_id)


class TestTmpFileSkipped:
    def test_tmp_file_not_read_as_profile(
        self, storage: VoiceProfileStorage
    ) -> None:
        pid = str(uuid.uuid4())
        profile_dir = storage.root_dir / pid
        profile_dir.mkdir(parents=True)
        # A stray .tmp file must not be treated as a profile.
        (profile_dir / (PROFILE_FILENAME + ".tmp")).write_text(
            json.dumps(_profile_dict()), encoding="utf-8"
        )
        # No profile.json -> not loadable, not listed.
        with pytest.raises(VoiceProfileNotFoundError):
            storage.load_profile(pid)
        assert list(storage.iter_profiles()) == []


class TestUnknownSchemaVersion:
    def test_unknown_schema_skipped_in_list(
        self, storage: VoiceProfileStorage
    ) -> None:
        good = _profile_dict()
        storage.save_profile(good)
        weird_id = str(uuid.uuid4())
        weird_dir = storage.root_dir / weird_id
        weird_dir.mkdir(parents=True)
        payload = _profile_dict()
        payload["id"] = weird_id
        payload["schema_version"] = 999
        (weird_dir / PROFILE_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        ids = {p["id"] for p in storage.iter_profiles()}
        assert good["id"] in ids
        assert weird_id not in ids

    def test_unknown_schema_raises_on_direct_load(
        self, storage: VoiceProfileStorage
    ) -> None:
        weird_id = str(uuid.uuid4())
        weird_dir = storage.root_dir / weird_id
        weird_dir.mkdir(parents=True)
        payload = _profile_dict()
        payload["id"] = weird_id
        payload["schema_version"] = 999
        (weird_dir / PROFILE_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        with pytest.raises(VoiceProfileStorageError):
            storage.load_profile(weird_id)


class TestPathTraversal:
    @pytest.mark.parametrize(
        "bad_id",
        [
            "..",
            "../foo",
            "..%2f",
            "not-a-uuid",
            "",
            "abc",
            "C:\\windows",
            "12345678-1234-1234-1234-123456789012/../x",
        ],
    )
    def test_invalid_id_rejected(
        self, storage: VoiceProfileStorage, bad_id: str
    ) -> None:
        with pytest.raises(VoiceProfileStorageError):
            storage.load_profile(bad_id)
        with pytest.raises(VoiceProfileStorageError):
            storage._profile_dir(bad_id)

    def test_non_canonical_uuid_rejected(self, storage: VoiceProfileStorage) -> None:
        # Uppercase hex is not the canonical str(uuid) form.
        non_canonical = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        with pytest.raises(VoiceProfileStorageError):
            storage.load_profile(non_canonical)


class TestDelete:
    def test_delete_removes_dir(self, storage: VoiceProfileStorage) -> None:
        payload = _profile_dict()
        storage.save_profile(payload)
        assert storage.delete_profile(payload["id"]) is True
        with pytest.raises(VoiceProfileNotFoundError):
            storage.load_profile(payload["id"])

    def test_delete_missing_returns_false(self, storage: VoiceProfileStorage) -> None:
        assert storage.delete_profile(str(uuid.uuid4())) is False

    def test_delete_only_removes_profile_dir(
        self, storage: VoiceProfileStorage, tmp_path: Path
    ) -> None:
        # Sibling directory must not be touched.
        sibling_id = str(uuid.uuid4())
        sibling_dir = storage.root_dir / sibling_id
        sibling_dir.mkdir(parents=True)
        (sibling_dir / "unrelated.txt").write_text("keep me", encoding="utf-8")

        payload = _profile_dict()
        storage.save_profile(payload)
        storage.delete_profile(payload["id"])

        assert sibling_dir.exists()
        assert (sibling_dir / "unrelated.txt").read_text(encoding="utf-8") == "keep me"
