"""Tests for the ASR preset system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_processing.asr_presets import (
    BUILTIN_PRESETS,
    FALLBACK_DEFAULT_PRESET_ID,
    LEGACY_CURRENT,
    MULTILINGUAL_LARGE_V3_NO_VAD,
    MULTILINGUAL_LARGE_V3_QUALITY,
    MULTILINGUAL_LARGE_V3_TURBO,
    AsrDefaultPresetStore,
    AsrPresetError,
    AsrPresetNotFoundError,
    check_preset_compatibility,
    faster_whisper_version,
    get_preset,
    is_production_eligible,
    list_presets,
)


def test_legacy_current_matches_runtime_defaults():
    """legacy-current must mirror the exact previous worker configuration."""
    p = LEGACY_CURRENT
    assert p.model == "large-v3"
    assert p.device == "cuda"
    assert p.compute_type == "int8_float16"
    assert p.language == "de"
    assert p.multilingual is False
    assert p.condition_on_previous_text is True
    assert p.vad_filter is True
    assert p.beam_size == 5
    assert p.word_timestamps is True
    assert p.hallucination_silence_threshold is None
    assert p.hotwords is None
    # The legacy worker never passed `multilingual` to transcribe().
    assert "multilingual" not in LEGACY_CURRENT.transcribe_kwargs()
    assert "hallucination_silence_threshold" not in LEGACY_CURRENT.transcribe_kwargs()


def test_multilingual_quality_preset():
    p = MULTILINGUAL_LARGE_V3_QUALITY
    assert p.id == "multilingual-large-v3-quality"
    assert p.model == "large-v3"
    assert p.compute_type == "float16"
    assert p.language is None
    assert p.multilingual is True
    assert p.condition_on_previous_text is False
    assert p.vad_filter is True
    assert p.hallucination_silence_threshold == 1.0
    kw = p.transcribe_kwargs()
    assert kw["language"] is None
    assert kw["multilingual"] is True
    assert kw["condition_on_previous_text"] is False
    assert kw["hallucination_silence_threshold"] == 1.0


def test_no_vad_preset_disables_vad():
    p = MULTILINGUAL_LARGE_V3_NO_VAD
    assert p.vad_filter is False
    assert p.multilingual is True
    assert p.language is None
    assert p.compute_type == "float16"
    # Otherwise identical to the quality preset.
    q = MULTILINGUAL_LARGE_V3_QUALITY
    assert p.model == q.model
    assert p.beam_size == q.beam_size
    assert p.hallucination_silence_threshold == q.hallucination_silence_threshold


def test_turbo_preset_uses_turbo_model():
    p = MULTILINGUAL_LARGE_V3_TURBO
    assert p.model == "large-v3-turbo"
    assert p.compute_type == "float16"
    assert p.multilingual is True
    assert p.language is None


def test_no_vad_cannot_be_production_default():
    assert is_production_eligible("multilingual-large-v3-no-vad") is False
    assert is_production_eligible("multilingual-large-v3-quality") is True
    assert is_production_eligible("legacy-current") is True
    assert is_production_eligible("multilingual-large-v3-turbo") is True


def test_unknown_preset_rejected():
    with pytest.raises(AsrPresetNotFoundError):
        get_preset("does-not-exist")
    assert is_production_eligible("does-not-exist") is False


def test_list_presets_contains_all_four():
    ids = {p["id"] for p in list_presets()}
    assert ids == {
        "legacy-current",
        "multilingual-large-v3-quality",
        "multilingual-large-v3-no-vad",
        "multilingual-large-v3-turbo",
    }


def test_transcribe_kwargs_omits_none_and_empty():
    kw = LEGACY_CURRENT.transcribe_kwargs()
    # vad_parameters only included when non-empty
    assert "vad_parameters" not in kw
    # hotwords only when set
    assert "hotwords" not in kw
    # no_speech_threshold not in kwargs when None
    assert "no_speech_threshold" not in kw


def test_default_store_falls_back_to_quality(tmp_path: Path):
    store = AsrDefaultPresetStore(tmp_path)
    selection = store.get()
    assert selection["preset_id"] == FALLBACK_DEFAULT_PRESET_ID
    assert selection["selected_at"] is None
    # No file written yet.
    assert not (tmp_path / "asr_default_preset.json").is_file()


def test_default_store_select_and_persist(tmp_path: Path):
    store = AsrDefaultPresetStore(tmp_path)
    store.select("multilingual-large-v3-turbo")
    # Reload from disk via a new instance.
    store2 = AsrDefaultPresetStore(tmp_path)
    sel = store2.get()
    assert sel["preset_id"] == "multilingual-large-v3-turbo"
    assert sel["selected_at"] is not None
    # The persisted file contains the full preset.
    payload = json.loads((tmp_path / "asr_default_preset.json").read_text(encoding="utf-8"))
    assert payload["preset"]["model"] == "large-v3-turbo"


def test_default_store_refuses_no_vad(tmp_path: Path):
    store = AsrDefaultPresetStore(tmp_path)
    with pytest.raises(AsrPresetError):
        store.select("multilingual-large-v3-no-vad")
    with pytest.raises(AsrPresetError):
        store.select("unknown")


def test_default_store_recovers_when_persisted_preset_no_longer_eligible(tmp_path: Path):
    path = tmp_path / "asr_default_preset.json"
    path.write_text(
        json.dumps({"preset_id": "multilingual-large-v3-no-vad", "selected_at": "x"}),
        encoding="utf-8",
    )
    store = AsrDefaultPresetStore(tmp_path)
    # Falls back to the safe default instead of returning the diagnostic preset.
    assert store.get()["preset_id"] == FALLBACK_DEFAULT_PRESET_ID


def test_faster_whisper_version_string():
    v = faster_whisper_version()
    # In the test env faster-whisper is installed; in CI without GPU it
    # may not be. Either is acceptable; we only assert the helper does not
    # raise and returns a str or None.
    assert v is None or isinstance(v, str)


def test_check_preset_compatibility_returns_list():
    reasons = check_preset_compatibility(MULTILINGUAL_LARGE_V3_QUALITY)
    assert isinstance(reasons, list)
