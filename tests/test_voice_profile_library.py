"""Tests for :mod:`voice_profiles.library`.

The script library is loaded from JSON files whose paths are injected through
the constructor, so every test builds small temporary fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_profiles.library import ScriptLibrary
from voice_profiles.schemas import (
    VoiceProfileStorageError,
    VoiceScriptNotFoundError,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _prompt(
    pid: str,
    order: int,
    *,
    category: str = "style",
    style: str = "neutral",
    text: str | None = None,
    tags: list[str] | None = None,
    recording_notes: str | None = None,
) -> dict:
    return {
        "id": pid,
        "order": order,
        "category": category,
        "style": style,
        "text": text or f"Text for {pid}",
        "recommended_duration_seconds": {"min": 5, "max": 12},
        "tags": tags or [],
        "recording_notes": recording_notes,
    }


def _pack(prompts: list[dict], *, schema_version: int = 1,
          expected_prompt_count: int | None = None,
          pack_id: str = "ttvturbo_voice_pack_v1",
          name: str = "TTVturbo Voice Pack v1") -> dict:
    return {
        "schema_version": schema_version,
        "id": pack_id,
        "locale": "de-DE",
        "name": name,
        "version": "v1",
        "expected_prompt_count": expected_prompt_count,
        "prompts": prompts,
    }


@pytest.fixture()
def two_prompts() -> list[dict]:
    return [
        _prompt("de-DE-neutral-001", 1),
        _prompt("de-DE-neutral-002", 2),
    ]


@pytest.fixture()
def pack_file(tmp_path: Path, two_prompts: list[dict]) -> Path:
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(_pack(two_prompts, expected_prompt_count=2), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def holdout_file(tmp_path: Path) -> Path:
    path = tmp_path / "holdout.json"
    holdout_prompts = [_prompt("de-DE-holdout-001", 1, style="holdout")]
    path.write_text(
        json.dumps(
            _pack(holdout_prompts, expected_prompt_count=1,
                  pack_id="ttvturbo_voice_holdout_v1",
                  name="TTVturbo Voice Holdout v1"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def library(pack_file: Path, holdout_file: Path) -> ScriptLibrary:
    return ScriptLibrary(pack_path=pack_file, holdout_path=holdout_file)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadPack:
    def test_load_valid_pack(self, library: ScriptLibrary) -> None:
        prompts = library.get_recording_prompts()
        assert [p.id for p in prompts] == ["de-DE-neutral-001", "de-DE-neutral-002"]
        assert prompts[0].text == "Text for de-DE-neutral-001"
        assert prompts[0].recommended_duration_seconds.min == 5
        assert prompts[0].recommended_duration_seconds.max == 12

    def test_get_prompt_by_id(self, library: ScriptLibrary) -> None:
        p = library.get_prompt("de-DE-neutral-001")
        assert p.id == "de-DE-neutral-001"
        assert p.order == 1

    def test_unknown_id_raises_typed(self, library: ScriptLibrary) -> None:
        with pytest.raises(VoiceScriptNotFoundError):
            library.get_prompt("does-not-exist")

    def test_holdout_separate_from_pack(self, library: ScriptLibrary) -> None:
        pack_ids = {p.id for p in library.get_recording_prompts()}
        holdout_ids = {p.id for p in library.get_holdout_prompts()}
        assert pack_ids == {"de-DE-neutral-001", "de-DE-neutral-002"}
        assert holdout_ids == {"de-DE-holdout-001"}
        assert pack_ids.isdisjoint(holdout_ids)
        # get_prompt finds holdout too
        assert library.get_prompt("de-DE-holdout-001").id == "de-DE-holdout-001"
        assert library.is_holdout_id("de-DE-holdout-001")
        assert not library.is_holdout_id("de-DE-neutral-001")

    def test_pack_metadata(self, library: ScriptLibrary) -> None:
        meta = library.get_pack_metadata()
        assert meta["id"] == "ttvturbo_voice_pack_v1"
        assert meta["locale"] == "de-DE"
        assert "prompts" not in meta


class TestSchemaValidation:
    def test_wrong_schema_version_rejected(self, tmp_path: Path, holdout_file: Path) -> None:
        bad = tmp_path / "bad_pack.json"
        bad.write_text(
            json.dumps(_pack([_prompt("x", 1)], schema_version=99), ensure_ascii=False),
            encoding="utf-8",
        )
        lib = ScriptLibrary(pack_path=bad, holdout_path=holdout_file)
        with pytest.raises(VoiceProfileStorageError):
            lib.get_recording_prompts()

    def test_missing_file_rejected(self, tmp_path: Path, holdout_file: Path) -> None:
        lib = ScriptLibrary(
            pack_path=tmp_path / "missing.json",
            holdout_path=holdout_file,
        )
        with pytest.raises(VoiceProfileStorageError):
            lib.get_recording_prompts()


class TestDuplicateIds:
    def test_duplicate_ids_rejected(self, tmp_path: Path, holdout_file: Path) -> None:
        bad = tmp_path / "dup.json"
        prompts = [_prompt("dup", 1), _prompt("dup", 2)]
        bad.write_text(
            json.dumps(_pack(prompts, expected_prompt_count=2), ensure_ascii=False),
            encoding="utf-8",
        )
        lib = ScriptLibrary(pack_path=bad, holdout_path=holdout_file)
        with pytest.raises(VoiceProfileStorageError):
            lib.get_recording_prompts()


class TestPromptCount:
    def test_wrong_prompt_count_rejected(self, tmp_path: Path, holdout_file: Path) -> None:
        bad = tmp_path / "count.json"
        prompts = [_prompt("a", 1), _prompt("b", 2)]
        # Declare 88 but provide 2
        bad.write_text(
            json.dumps(_pack(prompts, expected_prompt_count=88), ensure_ascii=False),
            encoding="utf-8",
        )
        lib = ScriptLibrary(pack_path=bad, holdout_path=holdout_file)
        with pytest.raises(VoiceProfileStorageError):
            lib.get_recording_prompts()

    def test_missing_expected_count_allowed(self, tmp_path: Path, holdout_file: Path) -> None:
        # If expected_prompt_count is null, no count check is performed.
        path = tmp_path / "pack.json"
        path.write_text(
            json.dumps(_pack([_prompt("a", 1)], expected_prompt_count=None),
                       ensure_ascii=False),
            encoding="utf-8",
        )
        lib = ScriptLibrary(pack_path=path, holdout_path=holdout_file)
        assert len(lib.get_recording_prompts()) == 1


class TestMalformedPrompt:
    def test_malformed_prompt_rejected(self, tmp_path: Path, holdout_file: Path) -> None:
        bad = tmp_path / "malformed.json"
        raw = _pack([{"id": "x", "order": 1}])  # missing required fields
        bad.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        lib = ScriptLibrary(pack_path=bad, holdout_path=holdout_file)
        with pytest.raises(VoiceProfileStorageError):
            lib.get_recording_prompts()
