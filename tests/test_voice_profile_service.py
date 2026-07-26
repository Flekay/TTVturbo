"""Tests for :mod:`voice_profiles.service`."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from voice_profiles.library import ScriptLibrary
from voice_profiles.schemas import (
    EXPECTED_PACK_PROMPT_COUNT,
    ReferenceStatus,
    VoiceProfileConflictError,
    VoiceProfileNotFoundError,
    VoiceProfileStorageError,
    VoiceProfileValidationError,
    VoiceScriptNotFoundError,
)
from voice_profiles.service import VoiceProfileService
from voice_profiles.storage import VoiceProfileStorage


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _prompt(pid: str, order: int, *, style: str = "neutral",
            category: str = "style", text: str | None = None) -> dict:
    return {
        "id": pid,
        "order": order,
        "category": category,
        "style": style,
        "text": text or f"Text for {pid}",
        "recommended_duration_seconds": {"min": 5, "max": 12},
        "tags": [],
        "recording_notes": None,
    }


def _pack_json(prompts: list[dict], *, pack_id: str = "ttvturbo-de-de-v1",
               title: str = "TTVturbo German Voice Pack v1",
               prompt_count: int | None = None,
               kind: str = "recording_pack") -> dict:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "locale": "de-DE",
        "kind": kind,
        "title": title,
        "description": "Test pack.",
        "prompt_count": prompt_count if prompt_count is not None else len(prompts),
        "prompts": prompts,
    }


def _quality(quality_class: str, *, eligible: bool | None = None,
             reasons: list[str] | None = None,
             warnings: list[str] | None = None) -> dict:
    """Build a valid analyzer-result dict for attach_reference."""
    if eligible is None:
        eligible = quality_class in ("EXCELLENT", "GOOD")
    return {
        "technical": {"sample_rate": 44100, "channels": 1, "frame_count": 44100,
                      "duration_seconds": 1.0},
        "quality": quality_class,
        "reasons": reasons or [],
        "warnings": warnings or [],
        "voice_clone_reference": {
            "eligible": eligible,
            "quality": quality_class,
            "reasons": reasons or [],
            "warnings": warnings or [],
        },
    }


@pytest.fixture()
def pack_path(tmp_path: Path) -> Path:
    prompts = [_prompt(f"de-DE-neutral-{i:03d}", i) for i in range(1, 4)]
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(_pack_json(prompts, prompt_count=3), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def holdout_path(tmp_path: Path) -> Path:
    prompts = [_prompt("de-DE-holdout-001", 1, style="holdout")]
    path = tmp_path / "holdout.json"
    path.write_text(
        json.dumps(_pack_json(prompts, prompt_count=1,
                              pack_id="ttvturbo-de-de-holdout-v1",
                              title="TTVturbo German Voice Holdout v1",
                              kind="holdout"),
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def recordings_dir(tmp_path: Path) -> Path:
    return tmp_path / "recordings"


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "voice_profiles_data"


@pytest.fixture()
def library(pack_path: Path, holdout_path: Path) -> ScriptLibrary:
    return ScriptLibrary(pack_path=pack_path, holdout_path=holdout_path)


@pytest.fixture()
def service(library: ScriptLibrary, recordings_dir: Path,
            data_dir: Path) -> VoiceProfileService:
    return VoiceProfileService(
        library=library,
        storage=VoiceProfileStorage(data_dir),
        recordings_dir=recordings_dir,
    )


@pytest.fixture()
def wav_file(recordings_dir: Path, make_real_wav) -> str:
    """Create a real WAV in recordings_dir and return its bare filename."""
    recordings_dir.mkdir(parents=True, exist_ok=True)
    name = f"ref_{uuid.uuid4().hex}.wav"
    make_real_wav(recordings_dir / name, duration=1.0)
    return name


# ===========================================================================
# Profile lifecycle
# ===========================================================================

class TestCreateProfile:
    def test_create_profile(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Meine Stimme")
        assert profile["name"] == "Meine Stimme"
        assert profile["locale"] == "de-DE"
        assert profile["archived"] is False
        assert profile["schema_version"] == 1
        # progress on a fresh profile
        assert profile["progress"]["total"] == 3
        assert profile["progress"]["missing"] == 3
        assert profile["progress"]["recorded"] == 0
        assert profile["progress"]["accepted"] == 0
        assert profile["progress"]["percentage"] == 0.0
        assert profile["progress"]["clone_ready"] is False
        assert profile["progress"]["pack_complete"] is False
        # id is a valid uuid
        uuid.UUID(profile["id"])

    @pytest.mark.parametrize("bad_name", ["", "   ", "\t\n", "\u200b\u200b"])
    def test_invalid_name_rejected(self, service: VoiceProfileService,
                                   bad_name: str) -> None:
        with pytest.raises(VoiceProfileValidationError):
            service.create_profile(bad_name)

    def test_name_too_long(self, service: VoiceProfileService) -> None:
        with pytest.raises(VoiceProfileValidationError):
            service.create_profile("x" * 81)

    def test_name_trimmed(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("  Hallo  ")
        assert profile["name"] == "Hallo"

    def test_unsupported_locale(self, service: VoiceProfileService) -> None:
        with pytest.raises(VoiceProfileValidationError):
            service.create_profile("Stimme", locale="en-US")


class TestRenameArchiveRestore:
    def test_rename(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Alt")
        renamed = service.rename_profile(profile["id"], "Neu")
        assert renamed["name"] == "Neu"
        again = service.get_profile(profile["id"])
        assert again["name"] == "Neu"

    def test_rename_invalid_name(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Alt")
        with pytest.raises(VoiceProfileValidationError):
            service.rename_profile(profile["id"], "   ")

    def test_archive_and_restore(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Stimme")
        archived = service.archive_profile(profile["id"])
        assert archived["archived"] is True
        # Hidden from default listing
        assert service.list_profiles() == []
        # Visible with include_archived
        listed = service.list_profiles(include_archived=True)
        assert len(listed) == 1
        # Restored
        restored = service.restore_profile(profile["id"])
        assert restored["archived"] is False
        assert len(service.list_profiles()) == 1

    def test_archived_hidden_by_default(self, service: VoiceProfileService) -> None:
        a = service.create_profile("A")
        b = service.create_profile("B")
        service.archive_profile(a["id"])
        ids = {p["id"] for p in service.list_profiles()}
        assert ids == {b["id"]}


class TestDeleteProfile:
    def test_delete(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Stimme")
        assert service.delete_profile(profile["id"]) is True
        with pytest.raises(VoiceProfileNotFoundError):
            service.get_profile(profile["id"])

    def test_delete_unknown_uuid(self, service: VoiceProfileService) -> None:
        with pytest.raises(VoiceProfileNotFoundError):
            service.delete_profile(str(uuid.uuid4()))

    def test_delete_keeps_wav(self, service: VoiceProfileService,
                              wav_file: str, recordings_dir: Path) -> None:
        profile = service.create_profile("Stimme")
        service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        assert (recordings_dir / wav_file).is_file()
        service.delete_profile(profile["id"])
        # The WAV must still exist.
        assert (recordings_dir / wav_file).is_file()


class TestUnknownUuid:
    def test_get_unknown(self, service: VoiceProfileService) -> None:
        with pytest.raises(VoiceProfileNotFoundError):
            service.get_profile(str(uuid.uuid4()))


class TestPathTraversal:
    @pytest.mark.parametrize("bad_id", ["..", "../x", "not-a-uuid", ""])
    def test_get_profile_rejects_bad_id(
        self, service: VoiceProfileService, bad_id: str
    ) -> None:
        with pytest.raises((VoiceProfileNotFoundError, VoiceProfileStorageError)):
            service.get_profile(bad_id)


# ===========================================================================
# References
# ===========================================================================

class TestAttachReference:
    def test_good_becomes_accepted(self, service: VoiceProfileService,
                                   wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        ref = out["references"]["de-DE-neutral-001"]
        assert ref["status"] == ReferenceStatus.ACCEPTED.value
        assert ref["quality_class"] == "GOOD"
        assert ref["recording_filename"] == wav_file
        assert ref["script_text"] == "Text for de-DE-neutral-001"
        assert ref["recording_sha256"]
        # progress reflects one accepted
        assert out["progress"]["accepted"] == 1
        assert out["progress"]["clone_ready"] is True

    def test_excellent_becomes_accepted(self, service: VoiceProfileService,
                                        wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("EXCELLENT")
        )
        assert out["references"]["de-DE-neutral-001"]["status"] == "ACCEPTED"

    def test_review_stays_review(self, service: VoiceProfileService,
                                 wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("REVIEW")
        )
        ref = out["references"]["de-DE-neutral-001"]
        assert ref["status"] == "REVIEW"
        assert ref["review_accepted"] is False
        assert out["progress"]["review"] == 1
        assert out["progress"]["accepted"] == 0
        assert out["progress"]["clone_ready"] is False

    def test_reject_stays_rejected(self, service: VoiceProfileService,
                                   wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("REJECT")
        )
        ref = out["references"]["de-DE-neutral-001"]
        assert ref["status"] == "REJECTED"
        assert out["progress"]["rejected"] == 1
        assert out["progress"]["clone_ready"] is False


class TestAcceptReview:
    def test_review_explicitly_accepted(self, service: VoiceProfileService,
                                        wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("REVIEW")
        )
        out = service.accept_review_reference(profile["id"], "de-DE-neutral-001")
        ref = out["references"]["de-DE-neutral-001"]
        assert ref["status"] == "ACCEPTED"
        assert ref["review_accepted"] is True
        assert out["progress"]["accepted"] == 1

    def test_reject_cannot_be_accepted(self, service: VoiceProfileService,
                                       wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("REJECT")
        )
        with pytest.raises(VoiceProfileConflictError):
            service.accept_review_reference(profile["id"], "de-DE-neutral-001")


class TestRecordingValidation:
    def test_missing_recording_rejected(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Stimme")
        with pytest.raises(VoiceProfileValidationError):
            service.attach_reference(
                profile["id"], "de-DE-neutral-001", "does_not_exist.wav",
                _quality("GOOD"),
            )

    @pytest.mark.parametrize(
        "bad_name",
        [
            "sub/dir.wav",
            "back\\slash.wav",
            "..",
            "hidden.wav",
            ".hidden.wav",
            "abs.wav",
            "noext",
            "file.WAV.txt",
        ],
    )
    def test_invalid_filename_rejected(
        self, service: VoiceProfileService, bad_name: str
    ) -> None:
        profile = service.create_profile("Stimme")
        with pytest.raises(VoiceProfileValidationError):
            service.attach_reference(
                profile["id"], "de-DE-neutral-001", bad_name, _quality("GOOD")
            )

    def test_sha256_computed_from_file(
        self, service: VoiceProfileService, wav_file: str,
        recordings_dir: Path,
    ) -> None:
        import hashlib
        h = hashlib.sha256()
        with open(recordings_dir / wav_file, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        expected = h.hexdigest()

        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        assert out["references"]["de-DE-neutral-001"]["recording_sha256"] == expected

    def test_caller_sha_not_trusted(
        self, service: VoiceProfileService, wav_file: str,
        recordings_dir: Path,
    ) -> None:
        """The service has no recording_sha256 parameter; SHA is always
        computed from the file on disk. We verify the parameter does not
        exist on the signature."""
        import inspect
        sig = inspect.signature(service.attach_reference)
        assert "recording_sha256" not in sig.parameters


class TestScriptTextFromLibrary:
    def test_text_comes_from_library(self, service: VoiceProfileService,
                                     wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        ref = out["references"]["de-DE-neutral-001"]
        assert ref["script_text"] == "Text for de-DE-neutral-001"
        assert ref["category"] == "style"
        assert ref["style"] == "neutral"

    def test_unknown_script_id_rejected(self, service: VoiceProfileService,
                                        wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        with pytest.raises(VoiceScriptNotFoundError):
            service.attach_reference(
                profile["id"], "does-not-exist", wav_file, _quality("GOOD")
            )

    def test_holdout_script_cannot_be_attached(
        self, service: VoiceProfileService, wav_file: str
    ) -> None:
        profile = service.create_profile("Stimme")
        with pytest.raises(VoiceProfileValidationError):
            service.attach_reference(
                profile["id"], "de-DE-holdout-001", wav_file, _quality("GOOD")
            )


class TestQualityValidation:
    def test_quality_not_dict(self, service: VoiceProfileService,
                              wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        with pytest.raises(VoiceProfileValidationError):
            service.attach_reference(
                profile["id"], "de-DE-neutral-001", wav_file, "not-a-dict"
            )

    def test_quality_missing_voice_clone_reference(
        self, service: VoiceProfileService, wav_file: str
    ) -> None:
        profile = service.create_profile("Stimme")
        with pytest.raises(VoiceProfileValidationError):
            service.attach_reference(
                profile["id"], "de-DE-neutral-001", wav_file, {"quality": "GOOD"}
            )

    def test_quality_missing_required_key(
        self, service: VoiceProfileService, wav_file: str
    ) -> None:
        profile = service.create_profile("Stimme")
        bad = {"voice_clone_reference": {"quality": "GOOD", "eligible": True,
                                         "reasons": []}}  # missing warnings
        with pytest.raises(VoiceProfileValidationError):
            service.attach_reference(
                profile["id"], "de-DE-neutral-001", wav_file, bad
            )

    def test_quality_invalid_class(
        self, service: VoiceProfileService, wav_file: str
    ) -> None:
        profile = service.create_profile("Stimme")
        bad = _quality("GOOD")
        bad["voice_clone_reference"]["quality"] = "BOGUS"
        with pytest.raises(VoiceProfileValidationError):
            service.attach_reference(
                profile["id"], "de-DE-neutral-001", wav_file, bad
            )


class TestReplaceAndDetach:
    def test_reference_replaced(self, service: VoiceProfileService,
                                recordings_dir: Path, make_real_wav) -> None:
        profile = service.create_profile("Stimme")
        name_a = f"a_{uuid.uuid4().hex}.wav"
        name_b = f"b_{uuid.uuid4().hex}.wav"
        make_real_wav(recordings_dir / name_a, 1.0)
        make_real_wav(recordings_dir / name_b, 1.0)

        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", name_a, _quality("GOOD")
        )
        first_attached = out["references"]["de-DE-neutral-001"]["attached_at"]

        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", name_b, _quality("EXCELLENT")
        )
        ref = out["references"]["de-DE-neutral-001"]
        assert ref["recording_filename"] == name_b
        assert ref["quality_class"] == "EXCELLENT"
        # attached_at preserved on replace
        assert ref["attached_at"] == first_attached
        # Only one reference for this script id
        assert len(out["references"]) == 1

    def test_reference_detached(self, service: VoiceProfileService,
                                wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        out = service.detach_reference(profile["id"], "de-DE-neutral-001")
        assert "de-DE-neutral-001" not in out["references"]
        assert out["progress"]["recorded"] == 0
        assert out["progress"]["accepted"] == 0

    def test_detach_missing_reference(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Stimme")
        with pytest.raises(VoiceScriptNotFoundError):
            service.detach_reference(profile["id"], "de-DE-neutral-001")


class TestFindProfilesUsingRecording:
    def test_find_profiles(self, service: VoiceProfileService, wav_file: str) -> None:
        a = service.create_profile("A")
        b = service.create_profile("B")
        service.attach_reference(
            a["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        service.attach_reference(
            b["id"], "de-DE-neutral-002", wav_file, _quality("GOOD")
        )
        found = service.find_profiles_using_recording(wav_file)
        ids = {p["id"] for p in found}
        assert ids == {a["id"], b["id"]}

    def test_find_none(self, service: VoiceProfileService, wav_file: str) -> None:
        service.create_profile("A")
        assert service.find_profiles_using_recording(wav_file) == []


class TestListAcceptedReferences:
    def test_list_accepted(self, service: VoiceProfileService,
                           recordings_dir: Path, make_real_wav) -> None:
        profile = service.create_profile("Stimme")
        w1 = f"a_{uuid.uuid4().hex}.wav"
        w2 = f"b_{uuid.uuid4().hex}.wav"
        make_real_wav(recordings_dir / w1, 1.0)
        make_real_wav(recordings_dir / w2, 1.0)
        service.attach_reference(
            profile["id"], "de-DE-neutral-001", w1, _quality("GOOD")
        )
        service.attach_reference(
            profile["id"], "de-DE-neutral-002", w2, _quality("REVIEW")
        )
        accepted = service.list_accepted_references(profile["id"])
        assert len(accepted) == 1
        assert accepted[0]["script_id"] == "de-DE-neutral-001"


# ===========================================================================
# Progress
# ===========================================================================

class TestProgress:
    def test_empty_profile(self, service: VoiceProfileService) -> None:
        profile = service.create_profile("Stimme")
        p = profile["progress"]
        assert p == {
            "total": 3, "missing": 3, "recorded": 0, "accepted": 0,
            "review": 0, "rejected": 0, "percentage": 0.0,
            "clone_ready": False, "pack_complete": False,
        }

    def test_one_accepted(self, service: VoiceProfileService,
                          wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        p = out["progress"]
        assert p["accepted"] == 1
        assert p["recorded"] == 1
        assert p["missing"] == 2
        assert p["clone_ready"] is True
        assert p["pack_complete"] is False
        # 1/3 = 33.3%
        assert p["percentage"] == pytest.approx(33.3, abs=0.1)

    def test_review_progress(self, service: VoiceProfileService,
                             wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("REVIEW")
        )
        p = out["progress"]
        assert p["review"] == 1
        assert p["accepted"] == 0
        assert p["clone_ready"] is False

    def test_reject_progress(self, service: VoiceProfileService,
                             wav_file: str) -> None:
        profile = service.create_profile("Stimme")
        out = service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("REJECT")
        )
        p = out["progress"]
        assert p["rejected"] == 1
        assert p["accepted"] == 0
        assert p["clone_ready"] is False

    def test_full_pack(self, service: VoiceProfileService,
                       recordings_dir: Path, make_real_wav) -> None:
        profile = service.create_profile("Stimme")
        for i in range(1, 4):
            name = f"r{i}_{uuid.uuid4().hex}.wav"
            make_real_wav(recordings_dir / name, 1.0)
            service.attach_reference(
                profile["id"], f"de-DE-neutral-{i:03d}", name, _quality("GOOD")
            )
        out = service.get_profile(profile["id"])
        p = out["progress"]
        assert p["accepted"] == 3
        assert p["missing"] == 0
        assert p["percentage"] == 100.0
        assert p["pack_complete"] is True
        assert p["clone_ready"] is True

    def test_holdout_does_not_affect_progress(
        self, service: VoiceProfileService, recordings_dir: Path, make_real_wav
    ) -> None:
        # Build a separate service whose pack includes a holdout id; the
        # library here already has de-DE-holdout-001 in the holdout file.
        # We cannot attach it (blocked), so we instead write a profile
        # directly to disk with a holdout reference and verify the service
        # ignores it for progress.
        profile = service.create_profile("Stimme")
        # Sneak a holdout reference into the on-disk profile via the storage
        # layer, bypassing attach_reference.
        raw = service.storage.load_profile(profile["id"])
        raw["references"]["de-DE-holdout-001"] = {
            "script_id": "de-DE-holdout-001",
            "script_text": "holdout",
            "category": "style",
            "style": "holdout",
            "recording_filename": "ignored.wav",
            "recording_sha256": "x",
            "quality": {},
            "quality_class": "GOOD",
            "status": "ACCEPTED",
            "review_accepted": True,
            "attached_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        service.storage.save_profile(raw)
        out = service.get_profile(profile["id"])
        # Holdout reference is still present in references...
        assert "de-DE-holdout-001" in out["references"]
        # ...but does not count toward pack progress.
        assert out["progress"]["total"] == 3
        assert out["progress"]["accepted"] == 0
        assert out["progress"]["recorded"] == 0
        assert out["progress"]["missing"] == 3
        assert out["progress"]["pack_complete"] is False


# ===========================================================================
# Persistence / restart
# ===========================================================================

class TestPersistence:
    def test_restart_with_new_service_instance(
        self, library: ScriptLibrary, recordings_dir: Path, data_dir: Path,
        wav_file: str,
    ) -> None:
        s1 = VoiceProfileService(
            library=library,
            storage=VoiceProfileStorage(data_dir),
            recordings_dir=recordings_dir,
        )
        profile = s1.create_profile("Stimme")
        s1.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        # Drop the in-memory service and build a fresh one pointing at the
        # same data dir.
        s2 = VoiceProfileService(
            library=library,
            storage=VoiceProfileStorage(data_dir),
            recordings_dir=recordings_dir,
        )
        listed = s2.list_profiles()
        assert len(listed) == 1
        assert listed[0]["id"] == profile["id"]
        assert listed[0]["references"]["de-DE-neutral-001"]["recording_filename"] == wav_file
        assert listed[0]["progress"]["accepted"] == 1

    def test_progress_not_persisted_as_stale(
        self, service: VoiceProfileService, data_dir: Path, wav_file: str
    ) -> None:
        """progress must not be stored on disk; it is always derived."""
        profile = service.create_profile("Stimme")
        service.attach_reference(
            profile["id"], "de-DE-neutral-001", wav_file, _quality("GOOD")
        )
        raw = service.storage.load_profile(profile["id"])
        assert "progress" not in raw
