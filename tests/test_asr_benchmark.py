"""Tests for the ASR benchmark service state machine.

These tests do NOT load real Whisper models. They cover:

* benchmark creation and validation;
* preset id validation (unknown preset rejected);
* input guardrails (hotwords/reference length);
* listing and get;
* cancel of a non-running benchmark;
* delete (and refusal while running);
* restart recovery (active benchmark marked FAILED);
* run finalisation (VAD diagnosis, metrics, flags) using a stubbed VAD;
* recommend_winner transparency.

The actual subprocess execution path is exercised by the E2E test gated
behind ``TTVTURBO_RUN_ASR_BENCHMARK_E2E=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from media_processing.asr_benchmark import (
    AsrBenchmarkError,
    AsrBenchmarkNotFoundError,
    AsrBenchmarkService,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_PARTIALLY_FAILED,
    STATUS_READY,
    finalise_run,
    recommend_winner,
)
from media_processing.asr_diagnostics import VadDiagnosis
from media_processing.asr_presets import MULTILINGUAL_LARGE_V3_QUALITY


class _FakeResolvedSource:
    def __init__(self, source_type: str, source_id: str) -> None:
        self.file_path = Path("/tmp/fake.mp4")
        self.file_name = "fake.mp4"
        self.title = "fake"
        self.duration_seconds = 15.0
        self.profile_id = None
        self.source_type = source_type
        self.source_id = source_id


class _FakeResolver:
    """Stand-in resolver that always reports the source as ready."""

    def resolve(self, source_type: str, source_id: str) -> Any:
        return _FakeResolvedSource(source_type, source_id)


@pytest.fixture()
def benchmark_service(tmp_path: Path) -> AsrBenchmarkService:
    from media_processing.gpu_lock import GpuLock
    return AsrBenchmarkService(
        data_dir=tmp_path,
        source_resolver=_FakeResolver(),  # type: ignore[arg-type]
        gpu_lock=GpuLock(tmp_path),
    )


def test_create_benchmark_validates_preset_ids(benchmark_service):
    with pytest.raises(AsrBenchmarkError):
        benchmark_service.create_benchmark(
            "twitch_clip", "src-1", preset_ids=["unknown"],
        )


def test_create_benchmark_requires_at_least_one_preset(benchmark_service):
    with pytest.raises(AsrBenchmarkError):
        benchmark_service.create_benchmark("twitch_clip", "src-1", preset_ids=[])


def test_create_benchmark_persists_record(benchmark_service):
    rec = benchmark_service.create_benchmark(
        "twitch_clip", "src-1",
        preset_ids=["legacy-current", "multilingual-large-v3-quality"],
        reference_text="Ich ganken jetzt",
        hotwords="Twitch Flash",
    )
    assert rec["status"] == "QUEUED"
    assert rec["selected_presets"] == ["legacy-current", "multilingual-large-v3-quality"]
    assert rec["reference_text"] == "Ich ganken jetzt"
    assert rec["hotwords"] == "Twitch Flash"
    # Persisted.
    got = benchmark_service.get_benchmark(rec["id"])
    assert got["id"] == rec["id"]


def test_create_benchmark_dedupes_presets(benchmark_service):
    rec = benchmark_service.create_benchmark(
        "twitch_clip", "src-1",
        preset_ids=["legacy-current", "legacy-current", "multilingual-large-v3-quality"],
    )
    assert rec["selected_presets"] == ["legacy-current", "multilingual-large-v3-quality"]


def test_create_benchmark_hotwords_length_guardrail(benchmark_service):
    with pytest.raises(AsrBenchmarkError):
        benchmark_service.create_benchmark(
            "twitch_clip", "src-1",
            preset_ids=["legacy-current"],
            hotwords="x" * 600,
        )


def test_create_benchmark_reference_length_guardrail(benchmark_service):
    with pytest.raises(AsrBenchmarkError):
        benchmark_service.create_benchmark(
            "twitch_clip", "src-1",
            preset_ids=["legacy-current"],
            reference_text="x" * 6000,
        )


def test_get_unknown_benchmark_raises(benchmark_service):
    with pytest.raises(AsrBenchmarkNotFoundError):
        benchmark_service.get_benchmark("does-not-exist")


def test_list_benchmarks_returns_records(benchmark_service):
    a = benchmark_service.create_benchmark("twitch_clip", "src-1", preset_ids=["legacy-current"])
    b = benchmark_service.create_benchmark("twitch_clip", "src-2", preset_ids=["legacy-current"])
    ids = {x["id"] for x in benchmark_service.list_benchmarks()}
    assert {a["id"], b["id"]} <= ids


def test_cancel_non_running_queued_benchmark(benchmark_service):
    rec = benchmark_service.create_benchmark("twitch_clip", "src-1", preset_ids=["legacy-current"])
    out = benchmark_service.cancel(rec["id"])
    assert out["status"] == STATUS_CANCELED


def test_delete_benchmark(benchmark_service):
    rec = benchmark_service.create_benchmark("twitch_clip", "src-1", preset_ids=["legacy-current"])
    assert benchmark_service.delete(rec["id"]) is True
    with pytest.raises(AsrBenchmarkNotFoundError):
        benchmark_service.get_benchmark(rec["id"])


def test_delete_unknown_raises(benchmark_service):
    with pytest.raises(AsrBenchmarkNotFoundError):
        benchmark_service.delete("unknown")


def test_restart_recovery_marks_active_failed(tmp_path: Path):
    from media_processing.gpu_lock import GpuLock
    svc = AsrBenchmarkService(tmp_path, _FakeResolver(), GpuLock(tmp_path))  # type: ignore[arg-type]
    rec = svc.create_benchmark("twitch_clip", "src-1", preset_ids=["legacy-current"])
    # Simulate a crash mid-run by writing RUNNING on disk.
    p = svc._benchmark_path(rec["id"])  # noqa: SLF001
    payload = json.loads(p.read_text(encoding="utf-8"))
    payload["status"] = "RUNNING"
    p.write_text(json.dumps(payload), encoding="utf-8")
    # New service instance simulates a restart.
    svc2 = AsrBenchmarkService(tmp_path, _FakeResolver(), GpuLock(tmp_path))  # type: ignore[arg-type]
    out = svc2.get_benchmark(rec["id"])
    assert out["status"] == STATUS_FAILED
    assert "interrupted" in (out.get("error") or "").lower()


def test_finalise_run_attaches_metrics_and_flags(tmp_path):
    # Stub the VAD computation so we don't need onnxruntime/silero.
    fake_vad = VadDiagnosis(
        audio_duration_seconds=10.0,
        duration_after_vad_seconds=3.0,
        removed_by_vad_seconds=7.0,
        speech_regions=[{"start": 0.0, "end": 3.0}],
    )
    run_payload = {
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "ich ganken jetzt",
             "no_speech_probability": 0.1, "avg_logprob": -0.3, "compression_ratio": 1.2},
        ],
        "audio_duration_seconds": 10.0,
        "transcript_text": "ich ganken jetzt",
    }
    with patch(
        "media_processing.asr_benchmark.compute_vad_regions",
        return_value=fake_vad,
    ):
        out = finalise_run(
            run_payload,
            audio_path="/tmp/fake.flac",
            preset=MULTILINGUAL_LARGE_V3_QUALITY,
            reference_text="ich ganken jetzt",
            compute_vad=True,
        )
    assert out["vad_diagnosis"]["computed"] is True
    assert out["vad_diagnosis"]["speech_regions"] == [{"start": 0.0, "end": 3.0}]
    assert out["metrics"]["available"] is True
    assert out["metrics"]["wer"] == 0.0
    # No hallucination flags expected for clean segment inside VAD region.
    assert out["hallucination_flags"] == []


def test_finalise_run_no_vad_skips_vad_computation(tmp_path):
    run_payload = {
        "segments": [{"id": 1, "start": 0.0, "end": 1.0, "text": "a"}],
        "audio_duration_seconds": 5.0,
    }
    out = finalise_run(
        run_payload,
        audio_path="/tmp/fake.flac",
        preset=MULTILINGUAL_LARGE_V3_QUALITY,
        reference_text=None,
        compute_vad=False,
    )
    assert out["vad_diagnosis"]["computed"] is False
    assert out["vad_diagnosis"]["speech_regions"] == []
    # No ground truth -> metrics available False (no reference).
    assert out["metrics"]["available"] is False


def test_recommend_winner_returns_lowest_wer():
    runs = [
        {"preset_id": "a", "metrics": {"available": True, "wer": 0.3, "insertions": 0, "deletions": 0}},
        {"preset_id": "b", "metrics": {"available": True, "wer": 0.1, "insertions": 0, "deletions": 0}},
    ]
    winner = recommend_winner(runs)
    assert winner is not None
    assert winner["preset_id"] == "b"


def test_recommend_winner_none_without_metrics():
    runs = [{"preset_id": "a", "metrics": {"available": False}}]
    assert recommend_winner(runs) is None
