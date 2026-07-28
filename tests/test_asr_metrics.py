"""Tests for ASR metrics (WER/CER) and normalisation."""

from __future__ import annotations

import pytest

from ttvturbo.media_processing.asr_metrics import (
    compute_metrics,
    hypothesis_text,
    normalise_text,
    rank_runs,
)


def test_normalise_preserves_umlauts_and_ss():
    s = "Ich gehe über die Straße nach München!"
    n = normalise_text(s)
    assert "ü" in n
    assert "ß" in n
    assert "!" not in n
    assert n == "ich gehe über die straße nach münchen"


def test_normalise_collapses_whitespace_and_lowercases():
    assert normalise_text("  Hello   WORLD  ") == "hello world"


def test_normalise_handles_none_and_non_str():
    assert normalise_text(None) == ""
    assert normalise_text(123) == "123"


def test_hypothesis_text_concatenates_segments():
    segs = [
        {"text": "  Ich  "},
        {"text": "gehe"},
        {"text": ""},
        {"text": "nach Hause"},
    ]
    assert hypothesis_text(segs) == "Ich gehe nach Hause"


def test_compute_metrics_identical_texts():
    m = compute_metrics("Ich ganken jetzt", "Ich ganken jetzt")
    assert m.available is True
    assert m.wer == 0.0
    assert m.cer == 0.0
    assert m.substitutions == 0
    assert m.deletions == 0
    assert m.insertions == 0


def test_compute_metrics_empty_reference_and_hypothesis():
    m = compute_metrics("", "")
    assert m.available is True
    assert m.wer == 0.0


def test_compute_metrics_empty_hypothesis_all_deletions():
    m = compute_metrics("eins zwei drei", "")
    assert m.available is True
    assert m.deletions == 3
    assert m.wer == 1.0


def test_compute_metrics_empty_reference_all_insertions():
    m = compute_metrics("", "eins zwei")
    assert m.available is True
    assert m.insertions == 2


def test_compute_metrics_substitution():
    m = compute_metrics("ich gehe jetzt", "ich gehe haus")
    assert m.available is True
    assert m.substitutions == 1
    assert m.deletions == 0
    assert m.insertions == 0


def test_compute_metrics_denglish_preserved():
    ref = "Ich glaube der Gegner hat gerade seinen Flash benutzt"
    hyp = "Ich glaube der Gegner hat gerade seinen Flash benutzt"
    m = compute_metrics(ref, hyp)
    assert m.wer == 0.0
    # English term "Flash" must not be translated or stripped.
    assert "flash" in m.reference_normalised
    assert "flash" in m.hypothesis_normalised


def test_compute_metrics_word_diff_present():
    m = compute_metrics("a b c", "a x c")
    types = [op["type"] for op in m.word_diff]
    assert "replace" in types
    # ref/hyp slices populated.
    replace_op = next(op for op in m.word_diff if op["type"] == "replace")
    assert replace_op["ref"] == ["b"]
    assert replace_op["hyp"] == ["x"]


def test_compute_metrics_returns_originals():
    ref = "Hallo, Welt!"
    hyp = "hallo welt"
    m = compute_metrics(ref, hyp)
    assert m.reference_original == ref
    assert m.hypothesis_original == hyp


def test_rank_runs_orders_by_wer_then_insertions_then_deletions_then_runtime():
    runs = [
        {"preset_id": "a", "metrics": {"available": True, "wer": 0.2, "insertions": 1, "deletions": 0},
         "runtime_seconds": 5.0},
        {"preset_id": "b", "metrics": {"available": True, "wer": 0.1, "insertions": 5, "deletions": 5},
         "runtime_seconds": 10.0},
        {"preset_id": "c", "metrics": {"available": True, "wer": 0.2, "insertions": 0, "deletions": 0},
         "runtime_seconds": 8.0},
        {"preset_id": "d", "metrics": {"available": False}, "runtime_seconds": 1.0},
    ]
    ranked = rank_runs(runs)
    # b has lowest WER -> first.
    assert ranked[0]["preset_id"] == "b"
    # c and a both WER=0.2; c has fewer insertions -> second.
    assert ranked[1]["preset_id"] == "c"
    assert ranked[2]["preset_id"] == "a"
    # d has no metrics -> last.
    assert ranked[-1]["preset_id"] == "d"


def test_rank_runs_no_metrics_keeps_original_order():
    runs = [
        {"preset_id": "x", "metrics": {"available": False}},
        {"preset_id": "y", "metrics": {"available": False}},
    ]
    ranked = rank_runs(runs)
    assert [r["preset_id"] for r in ranked] == ["x", "y"]
