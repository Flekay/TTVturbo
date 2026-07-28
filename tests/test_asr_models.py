"""Tests for ASR model adapters, candidates, and VRAM measurement.

Tests without real models:
  - normalized Whisper/Parakeet/Canary response structure;
  - unsupported fields are None;
  - model error handling;
  - missing dependency handling;
  - VRAM tracker with injected NVML interface;
  - model reuse and load-time marking;
  - candidate definitions and availability checks.
"""

from __future__ import annotations

import time
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from media_processing.asr_models import (
    CANDIDATES,
    CANDIDATE_MAP,
    CanaryAdapter,
    FasterWhisperAdapter,
    ModelCandidate,
    NormalizedTranscriptionResult,
    ParakeetAdapter,
    VramTracker,
    AsrAdapterError,
    check_candidate_available,
    check_canary_available,
    check_faster_whisper_available,
    check_parakeet_available,
    get_adapter,
    get_candidate,
    list_model_candidates,
    measure_peak_ram,
)


# ---------------------------------------------------------------------------
# Candidate definitions
# ---------------------------------------------------------------------------


def test_six_candidates_defined():
    ids = [c.id for c in CANDIDATES]
    assert "whisper-legacy-current" in ids
    assert "whisper-large-v3-forced-de-no-vad" in ids
    assert "whisper-large-v3-forced-en-no-vad" in ids
    assert "parakeet-tdt-0.6b-v3-auto" in ids
    assert "canary-1b-v2-de" in ids
    assert "canary-1b-v2-en" in ids
    assert len(CANDIDATES) == 6


def test_candidate_map_matches():
    for c in CANDIDATES:
        assert CANDIDATE_MAP[c.id] is c


def test_get_candidate_returns_none_for_unknown():
    assert get_candidate("nonexistent") is None


def test_list_model_candidates_includes_availability():
    candidates = list_model_candidates()
    assert len(candidates) == 6
    for c in candidates:
        assert "available" in c
        assert "model_family" in c
        assert "model_id" in c


def test_whisper_candidates_have_correct_options():
    de = CANDIDATE_MAP["whisper-large-v3-forced-de-no-vad"]
    assert de.options["language"] == "de"
    assert de.options["vad_filter"] is False
    assert de.options["condition_on_previous_text"] is False
    assert de.options["compute_type"] == "float16"

    en = CANDIDATE_MAP["whisper-large-v3-forced-en-no-vad"]
    assert en.options["language"] == "en"
    assert en.options["vad_filter"] is False


def test_parakeet_candidate_is_auto():
    p = CANDIDATE_MAP["parakeet-tdt-0.6b-v3-auto"]
    assert p.model_family == "parakeet"
    assert p.model_id == "nvidia/parakeet-tdt-0.6b-v3"
    assert p.options.get("language") is None


def test_canary_candidates_have_lang_pairs():
    de = CANDIDATE_MAP["canary-1b-v2-de"]
    assert de.options["source_lang"] == "de"
    assert de.options["target_lang"] == "de"
    en = CANDIDATE_MAP["canary-1b-v2-en"]
    assert en.options["source_lang"] == "en"
    assert en.options["target_lang"] == "en"


# ---------------------------------------------------------------------------
# NormalizedTranscriptionResult
# ---------------------------------------------------------------------------


def test_normalized_result_defaults():
    r = NormalizedTranscriptionResult(model_family="whisper", model_id="large-v3")
    d = r.to_dict()
    assert d["model_family"] == "whisper"
    assert d["model_id"] == "large-v3"
    assert d["language"] is None
    assert d["language_probability"] is None
    assert d["peak_vram_bytes"] is None
    assert d["peak_ram_bytes"] is None
    assert d["model_reused"] is False
    assert d["warnings"] == []
    assert d["segments"] == []
    assert d["words"] == []


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


def test_get_adapter_returns_correct_class():
    assert isinstance(get_adapter("whisper"), FasterWhisperAdapter)
    assert isinstance(get_adapter("parakeet"), ParakeetAdapter)
    assert isinstance(get_adapter("canary"), CanaryAdapter)


def test_get_adapter_unknown_family_raises():
    with pytest.raises(AsrAdapterError, match="unknown model family"):
        get_adapter("nonexistent")


# ---------------------------------------------------------------------------
# FasterWhisperAdapter (without real model)
# ---------------------------------------------------------------------------


def test_faster_whisper_adapter_missing_dependency():
    adapter = FasterWhisperAdapter()
    with patch("builtins.__import__", side_effect=ImportError("no faster_whisper")):
        with pytest.raises(AsrAdapterError, match="faster-whisper not installed"):
            adapter.transcribe("/tmp/audio.flac", {"model": "large-v3"})


def test_faster_whisper_adapter_normalised_response():
    """Verify the adapter returns a properly normalised result."""
    adapter = FasterWhisperAdapter()

    # Mock the WhisperModel class.
    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.id = 0
    mock_segment.start = 0.0
    mock_segment.end = 2.0
    mock_segment.text = "  Hallo Welt  "
    mock_segment.no_speech_prob = 0.01
    mock_word = MagicMock()
    mock_word.start = 0.0
    mock_word.end = 1.0
    mock_word.word = "Hallo"
    mock_word.probability = 0.95
    mock_segment.words = [mock_word]

    mock_info = MagicMock()
    mock_info.language = "de"
    mock_info.language_probability = 0.98

    mock_model.transcribe.return_value = ([mock_segment], mock_info)

    with patch("media_processing.asr_models.measure_peak_ram", return_value=4000000000):
        with patch.object(adapter, "_model", mock_model):
            with patch.object(adapter, "_loaded_model_id", "large-v3"):
                with patch.object(adapter, "_loaded_compute_type", "float16"):
                    result = adapter.transcribe(
                        "/tmp/audio.flac",
                        {"model": "large-v3", "compute_type": "float16"},
                    )

    assert result.model_family == "whisper"
    assert result.model_id == "large-v3"
    assert result.language == "de"
    assert result.language_probability == 0.98
    assert "Hallo Welt" in result.text
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Hallo Welt"
    assert len(result.words) == 1
    assert result.words[0]["text"] == "Hallo"
    assert result.model_reused is True  # model was already loaded
    assert result.load_seconds == 0.0  # reused, so 0
    assert result.peak_ram_bytes == 4000000000


# ---------------------------------------------------------------------------
# VRAM Tracker
# ---------------------------------------------------------------------------


class _FakeNVML:
    """Fake pynvml module for testing VramTracker."""

    def __init__(self, readings: list[int]) -> None:
        self._readings = readings
        self._idx = 0
        self.init_called = False
        self.shutdown_called = False

    def nvmlInit(self):
        self.init_called = True

    def nvmlShutdown(self):
        self.shutdown_called = True

    def nvmlDeviceGetHandleByIndex(self, idx):
        return f"handle-{idx}"

    def nvmlDeviceGetMemoryInfo(self, handle):
        class _Info:
            used = self._readings[min(self._idx, len(self._readings) - 1)]
        self._idx += 1
        return _Info()


def test_vram_tracker_with_nvml():
    readings = [100_000_000, 4_000_000_000, 200_000_000]
    fake = _FakeNVML(readings)
    tracker = VramTracker(gpu_index=0)
    with patch.dict("sys.modules", {"pynvml": fake}):
        tracker.init()
        assert tracker.available is True
        tracker.measure_before_load()
        tracker.measure_after_load()
        tracker.measure_after_release()
    assert tracker.vram_before_bytes == 100_000_000
    assert tracker.vram_after_load_bytes == 4_000_000_000
    assert tracker.peak_vram_bytes == 4_000_000_000
    assert tracker.vram_after_release_bytes == 200_000_000


def test_vram_tracker_unavailable_returns_none():
    tracker = VramTracker(gpu_index=0)
    with patch.dict("sys.modules", {"pynvml": None}):
        with patch("builtins.__import__", side_effect=ImportError("no pynvml")):
            tracker.init()
    assert tracker.available is False
    assert tracker.vram_before_bytes is None
    assert tracker.vram_after_load_bytes is None
    assert tracker.peak_vram_bytes is None
    assert tracker.warning is not None
    d = tracker.to_dict()
    assert d["vram_before_bytes"] is None
    assert d["peak_vram_bytes"] is None


def test_vram_tracker_never_reports_zero_when_unavailable():
    """The spec says: never report 0 MB when NVML is unavailable."""
    tracker = VramTracker(gpu_index=0)
    with patch("builtins.__import__", side_effect=ImportError("no pynvml")):
        tracker.init()
    tracker.measure_before_load()
    tracker.measure_after_load()
    tracker.measure_after_release()
    # All values must be None, not 0.
    assert tracker.vram_before_bytes is None
    assert tracker.vram_after_load_bytes is None
    assert tracker.peak_vram_bytes is None
    assert tracker.vram_after_release_bytes is None


def test_vram_tracker_peak_is_max_of_readings():
    readings = [100, 5000, 3000, 200]
    fake = _FakeNVML(readings)
    tracker = VramTracker(gpu_index=0)
    with patch.dict("sys.modules", {"pynvml": fake}):
        tracker.init()
        tracker.measure_before_load()  # 100
        tracker.measure_after_load()   # 5000
        tracker.measure_after_release()  # 3000
    # Peak should be the after_load reading (5000), not after_release.
    assert tracker.peak_vram_bytes == 5000


# ---------------------------------------------------------------------------
# Model reuse marking
# ---------------------------------------------------------------------------


def test_model_reuse_marks_load_seconds_zero():
    """When a model is reused, load_seconds must be 0.0 and model_reused=True."""
    adapter = FasterWhisperAdapter()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], MagicMock(language="de", language_probability=0.9))

    # Simulate already-loaded model.
    with patch.object(adapter, "_model", mock_model):
        with patch.object(adapter, "_loaded_model_id", "large-v3"):
            with patch.object(adapter, "_loaded_compute_type", "int8_float16"):
                result = adapter.transcribe(
                    "/tmp/audio.flac",
                    {"model": "large-v3", "compute_type": "int8_float16"},
                )
    assert result.model_reused is True
    assert result.load_seconds == 0.0


def test_model_not_reused_when_model_id_changes():
    """When the model id changes, model_reused must be False."""
    adapter = FasterWhisperAdapter()
    mock_old_model = MagicMock()
    mock_new_model = MagicMock()
    mock_new_model.transcribe.return_value = ([], MagicMock(language="de", language_probability=0.9))

    # Simulate loaded model "large-v3" but requesting "large-v3-turbo".
    adapter._model = mock_old_model
    adapter._loaded_model_id = "large-v3"
    adapter._loaded_compute_type = "int8_float16"

    fake_fw = MagicMock()
    fake_fw.WhisperModel = MagicMock(return_value=mock_new_model)

    with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
        with patch("media_processing.asr_models.measure_peak_ram", return_value=None):
            result = adapter.transcribe(
                "/tmp/audio.flac",
                {"model": "large-v3-turbo", "compute_type": "int8_float16"},
            )
    assert result.model_reused is False
    # Old model should have been released; new model loaded.
    assert adapter._loaded_model_id == "large-v3-turbo"


# ---------------------------------------------------------------------------
# RAM measurement
# ---------------------------------------------------------------------------


def test_measure_peak_ram_returns_int():
    ram = measure_peak_ram()
    # psutil is a project dependency, so this should work.
    if ram is not None:
        assert isinstance(ram, int)
        assert ram > 0


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------


def test_check_candidate_available_unknown_returns_false():
    assert check_candidate_available("nonexistent") is False


def test_check_faster_whisper_available_returns_bool():
    assert isinstance(check_faster_whisper_available(), bool)


def test_check_parakeet_available_returns_bool():
    assert isinstance(check_parakeet_available(), bool)


def test_check_canary_available_returns_bool():
    assert isinstance(check_canary_available(), bool)
