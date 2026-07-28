"""Tests for ASR diagnostics: hallucination and missing-speech flags."""

from __future__ import annotations

from media_processing.asr_diagnostics import (
    VadDiagnosis,
    flag_hallucinations,
    flag_missing_speech,
)


def _vad(regions):
    return VadDiagnosis(
        audio_duration_seconds=10.0,
        duration_after_vad_seconds=sum(r["end"] - r["start"] for r in regions),
        removed_by_vad_seconds=10.0 - sum(r["end"] - r["start"] for r in regions),
        speech_regions=regions,
    )


def test_text_outside_vad_region_flagged():
    vad = _vad([{"start": 0.0, "end": 2.0}])
    segs = [{"id": 1, "start": 5.0, "end": 6.0, "text": "halluciniert"}]
    flags = flag_hallucinations(segs, vad)
    types = [f["type"] for f in flags]
    assert "TEXT_OUTSIDE_VAD_REGION" in types


def test_text_inside_vad_region_not_flagged():
    vad = _vad([{"start": 0.0, "end": 5.0}])
    segs = [{"id": 1, "start": 1.0, "end": 2.0, "text": "real speech"}]
    flags = flag_hallucinations(segs, vad)
    assert not any(f["type"] == "TEXT_OUTSIDE_VAD_REGION" for f in flags)


def test_high_no_speech_probability_flagged():
    segs = [{"id": 1, "start": 0.0, "end": 1.0, "text": "text",
             "no_speech_probability": 0.9}]
    flags = flag_hallucinations(segs, None)
    assert any(f["type"] == "HIGH_NO_SPEECH_PROBABILITY" for f in flags)


def test_low_logprob_flagged():
    segs = [{"id": 1, "start": 0.0, "end": 1.0, "text": "text",
             "avg_logprob": -1.5}]
    flags = flag_hallucinations(segs, None)
    assert any(f["type"] == "LOW_LOGPROB" for f in flags)


def test_high_compression_ratio_flagged():
    segs = [{"id": 1, "start": 0.0, "end": 1.0, "text": "text",
             "compression_ratio": 3.0}]
    flags = flag_hallucinations(segs, None)
    assert any(f["type"] == "HIGH_COMPRESSION_RATIO" for f in flags)


def test_repeated_segment_text_flagged():
    segs = [
        {"id": 1, "start": 0.0, "end": 1.0, "text": "eindeutig wiederholter Text"},
        {"id": 2, "start": 1.0, "end": 2.0, "text": "eindeutig wiederholter Text"},
    ]
    flags = flag_hallucinations(segs, None)
    assert any(f["type"] == "REPEATED_SEGMENT_TEXT" for f in flags)


def test_ground_truth_insertions_flagged():
    flags = flag_hallucinations([], None, metrics={"available": True, "insertions": 3})
    assert any(f["type"] == "GROUND_TRUTH_INSERTIONS" for f in flags)


def test_speech_region_without_transcript_flagged():
    vad = _vad([{"start": 0.0, "end": 3.0}])
    segs = [{"id": 1, "start": 5.0, "end": 6.0, "text": "woanders"}]
    flags = flag_missing_speech(segs, vad, audio_duration=10.0, metrics=None)
    assert any(f["type"] == "SPEECH_REGION_WITHOUT_TRANSCRIPT" for f in flags)


def test_ground_truth_deletions_flagged():
    flags = flag_missing_speech([], None, audio_duration=10.0,
                                metrics={"available": True, "deletions": 2})
    assert any(f["type"] == "GROUND_TRUTH_DELETIONS" for f in flags)


def test_empty_segment_over_long_region_flagged():
    segs = [{"id": 1, "start": 0.0, "end": 3.0, "text": ""}]
    flags = flag_missing_speech(segs, None, audio_duration=10.0, metrics=None)
    assert any(f["type"] == "EMPTY_SEGMENT_OVER_LONG_REGION" for f in flags)


def test_short_hypothesis_for_speech_duration_flagged():
    vad = _vad([{"start": 0.0, "end": 5.0}])
    segs = [{"id": 1, "start": 0.0, "end": 5.0, "text": "a"}]
    flags = flag_missing_speech(segs, vad, audio_duration=10.0, metrics=None)
    assert any(f["type"] == "SHORT_HYPOTHESIS_FOR_SPEECH_DURATION" for f in flags)


def test_no_false_best_recommendation_without_ground_truth():
    """Heuristic flags must not claim a winner without ground truth."""
    from media_processing.asr_benchmark import recommend_winner
    runs = [
        {"preset_id": "a", "metrics": {"available": False}},
        {"preset_id": "b", "metrics": {"available": False}},
    ]
    assert recommend_winner(runs) is None
