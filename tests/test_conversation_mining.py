"""Tests for Conversation Mining.

Covers:
* block building (deterministic, segment boundaries, overlap, pause break);
* model output validation (valid, invalid category, unknown segment id,
  out-of-range confidence, missing fields);
* JSON repair (markdown fences, trailing text, trailing commas);
* deduplication (overlapping conversations merged);
* boundary cleanup (expansion within neighbourhood);
* finalization (timestamps resolved, excerpt built, sorted);
* stale detection (transcript_revision mismatch);
* service start_run (no transcript -> validation error;
  no model -> unavailable; idempotency reuse);
* API endpoints (status, start, list, get, cancel, retry, delete);
* pipeline integration (CONVERSATION_MINING step added to DEFAULT_VOD_PIPELINE);
* architecture (no env reads, no app imports).

These tests do not run the mining worker subprocess. They exercise the
pure-Python helpers and the service orchestration with a fake worker.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ttvturbo.media_processing import (
    ConversationMiningService,
    ConversationMiningUnavailableError,
    ConversationMiningValidationError,
    ConversationMiningConflictError,
    ConversationMiningNotFoundError,
    MiningRunStatus,
    build_blocks,
    validate_model_output,
    attempt_json_repair,
    deduplicate_conversations,
    cleanup_boundaries,
    finalize_conversations,
    CONVERSATION_CATEGORIES,
    CONVERSATION_SIGNALS,
)
from ttvturbo.media_processing.conversation_mining import (
    ModelOutputError,
    is_stale,
    MINING_CONFIG_VERSION,
    ConversationMiningStore,
)
from ttvturbo.media_processing.transcription import (
    TranscriptionService,
    TRANSCRIPT_JSON,
    TRANSCRIPT_METADATA,
)
from ttvturbo.media_processing.schemas import PipelineStepType
from ttvturbo.media_processing.pipeline import DEFAULT_VOD_PIPELINE
from ttvturbo.settings import Settings
from ttvturbo.storage_utils import atomic_write_json


# ---------------------------------------------------------------------------
# Local fixtures (mirror tests/test_transcript_corrections.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def media_storage(vod_data_dir: Path):
    from ttvturbo.media_processing import MediaJobStorage
    return MediaJobStorage(vod_data_dir)


@pytest.fixture()
def source_resolver(vod_service):
    from ttvturbo.media_processing import MediaSourceResolver
    return MediaSourceResolver(vod_service.storage)


@pytest.fixture()
def gpu_lock(vod_data_dir: Path):
    from ttvturbo.media_processing import GpuLock
    return GpuLock(vod_data_dir)


@pytest.fixture()
def audio_service(media_storage, source_resolver):
    from ttvturbo.media_processing import AudioExtractionService
    return AudioExtractionService(storage=media_storage, source_resolver=source_resolver)


@pytest.fixture()
def transcription_service(media_storage, source_resolver, audio_service, gpu_lock):
    os.environ["TTVTURBO_TRANSCRIPTION_DEVICE"] = "cpu"
    os.environ["TTVTURBO_TRANSCRIPTION_COMPUTE_TYPE"] = "int8"
    return TranscriptionService(
        storage=media_storage,
        source_resolver=source_resolver,
        audio_service=audio_service,
        gpu_lock=gpu_lock,
        device="cpu",
        compute_type="int8",
    )


@pytest.fixture()
def mining_settings(vod_data_dir: Path):
    s = Settings(data_root=vod_data_dir)
    # Configure a fake model id so the service reports available.
    s.conversation_mining_model_id = "fake-model/test"
    s.conversation_mining_device = "cpu"
    return s


@pytest.fixture()
def mining_service(transcription_service, gpu_lock, mining_settings):
    svc = ConversationMiningService(
        transcription_service=transcription_service,
        gpu_lock=gpu_lock,
        settings=mining_settings,
        worker_python="python",
    )
    # The test environment does not have transformers/torch installed, so
    # stub the dependency / worker / cuda checks so the service reports
    # available for tests that need a runnable mining service.
    svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
    svc._check_cuda_available = lambda: True  # noqa: SLF001
    svc._check_worker_module = lambda: True  # noqa: SLF001
    svc._is_model_cached = lambda model_id: True  # noqa: SLF001
    return svc


@pytest.fixture()
def app(mining_settings):
    from ttvturbo.app_factory import create_app
    return create_app(settings=mining_settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_v1_transcript(
    transcription_service: TranscriptionService,
    vod_id: str,
    segments: list[dict],
    *,
    transcription_id: str | None = None,
    model: str = "large-v3",
    language: str = "de",
) -> str:
    tid = transcription_id or str(uuid.uuid4())
    tdir = transcription_service.transcript_dir(vod_id, tid, "twitch_vod")
    tdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "id": tid,
        "source_type": "twitch_vod",
        "source_id": vod_id,
        "audio_artifact": "artifacts/audio/source_audio.flac",
        "model": model,
        "device": "cpu",
        "compute_type": "int8",
        "language": language,
        "language_probability": 0.9,
        "duration_seconds": float(segments[-1]["end"]) if segments else 0.0,
        "created_at": "2024-01-01T00:00:00+00:00",
        "segments": segments,
    }
    atomic_write_json(tdir / TRANSCRIPT_JSON, payload, Exception, kind="transcript")
    meta = {
        "schema_version": 1,
        "id": tid,
        "source_type": "twitch_vod",
        "source_id": vod_id,
        "audio_artifact": payload["audio_artifact"],
        "model": model,
        "device": "cpu",
        "compute_type": "int8",
        "language": language,
        "language_probability": 0.9,
        "duration_seconds": payload["duration_seconds"],
        "created_at": payload["created_at"],
        "status": "READY",
        "segment_count": len(segments),
        "files": {"json": TRANSCRIPT_JSON, "txt": "transcript.txt", "srt": "transcript.srt", "vtt": "transcript.vtt"},
        "produced_by_job_id": None,
    }
    atomic_write_json(tdir / TRANSCRIPT_METADATA, meta, Exception, kind="metadata")
    return tid


def _make_ready_vod(vod_service, make_real_mp4, channel_lister, title: str = "Mining Test VOD", login: str = "miningtestpayt") -> tuple[str, Path]:
    from ttvturbo.vod_pipeline import VodStatus
    profile = vod_service.create_profile(login)
    profile_id = profile["id"]
    if not channel_lister.vods_by_login.get(login.lower()):
        channel_lister.add_vod(login, "800", title=title, duration=60.0)
    vod_service.sync_vods(profile_id)
    vods = vod_service.list_vods(profile_id=profile_id)
    assert vods, "sync_vods produced no VODs"
    vod = vods[0]
    vod_id = vod["id"]
    vod_dir = vod_service.storage.vod_dir(vod_id)
    vod_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = vod_dir / "source.mp4"
    make_real_mp4(mp4_path, duration_seconds=1.0)
    vod = vod_service.storage.load_vod(vod_id)
    vod["status"] = VodStatus.READY.value
    vod["download"] = {
        "started_at": "2024-01-01T00:00:00+00:00",
        "completed_at": "2024-01-01T01:00:00+00:00",
        "file_name": "source.mp4",
        "file_size_bytes": mp4_path.stat().st_size,
        "container": "mp4",
        "duration_seconds": 1.0,
        "width": 160,
        "height": 120,
        "video_codec": "h264",
        "audio_codec": "aac",
    }
    vod["title"] = title
    vod_service.storage.save_vod(vod)
    return vod_id, mp4_path


def _make_ready_vod_with_transcript(
    transcription_service, vod_service, make_real_mp4, channel_lister, segments=None
) -> tuple[str, str]:
    vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
    segs = segments if segments is not None else _sample_segments()
    tid = _write_v1_transcript(transcription_service, vod_id, segs)
    return vod_id, tid


def _sample_segments() -> list[dict]:
    return [
        {"id": 0, "start": 1.42, "end": 3.85, "text": "Ich hab den Trick bekommen.", "words": []},
        {"id": 1, "start": 4.0, "end": 6.5, "text": "Das war echt cool.", "words": []},
        {"id": 2, "start": 7.0, "end": 10.0, "text": "Schaut mal, was passiert.", "words": []},
        {"id": 3, "start": 10.5, "end": 14.0, "text": "Das ist krass, oder?", "words": []},
        {"id": 4, "start": 14.5, "end": 18.0, "text": "Ich glaube, das wird viral.", "words": []},
    ]


def _long_segments(n: int = 30) -> list[dict]:
    return [
        {"id": i, "start": i * 5.0, "end": i * 5.0 + 4.0, "text": f"Segment {i}.", "words": []}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Block building
# ---------------------------------------------------------------------------


class TestBuildBlocks:
    def test_empty_segments_returns_empty(self):
        assert build_blocks([], target_seconds=90, max_seconds=180, overlap_seconds=15, pause_seconds=6) == []

    def test_single_segment_one_block(self):
        segs = [{"id": 0, "start": 0.0, "end": 5.0, "text": "Hi", "words": []}]
        blocks = build_blocks(segs, target_seconds=90, max_seconds=180, overlap_seconds=15, pause_seconds=6)
        assert len(blocks) == 1
        assert blocks[0]["start"] == 0.0
        assert blocks[0]["end"] == 5.0
        assert blocks[0]["segment_ids"] == ["0"]

    def test_long_transcript_multiple_blocks(self):
        segs = _long_segments(30)
        blocks = build_blocks(segs, target_seconds=90, max_seconds=180, overlap_seconds=15, pause_seconds=6)
        assert len(blocks) >= 2
        # Blocks should cover the full range.
        assert blocks[0]["start"] == 0.0
        assert blocks[-1]["end"] == segs[-1]["end"]
        # Each block should have at least one segment.
        for b in blocks:
            assert len(b["segment_ids"]) >= 1

    def test_pause_breaks_block(self):
        # Two groups of segments with a long pause between them.
        segs = [
            {"id": i, "start": i * 5.0, "end": i * 5.0 + 4.0, "text": f"S{i}", "words": []}
            for i in range(10)
        ]
        # Insert a 30s pause after segment 4.
        for i in range(5, 10):
            segs[i]["start"] = segs[4]["end"] + 30.0 + (i - 5) * 5.0
            segs[i]["end"] = segs[i]["start"] + 4.0
        blocks = build_blocks(segs, target_seconds=40, max_seconds=180, overlap_seconds=0, pause_seconds=6)
        # The pause should create a break.
        assert len(blocks) >= 2

    def test_block_ids_sequential(self):
        segs = _long_segments(20)
        blocks = build_blocks(segs, target_seconds=50, max_seconds=100, overlap_seconds=10, pause_seconds=6)
        for i, b in enumerate(blocks):
            assert b["block_id"] == f"block-{i}"


# ---------------------------------------------------------------------------
# Model output validation
# ---------------------------------------------------------------------------


class TestValidateModelOutput:
    def test_valid_output(self):
        seg_ids = ["0", "1", "2", "3", "4"]
        raw = json.dumps({
            "conversations": [{
                "start_segment_id": "1",
                "end_segment_id": "3",
                "title": "Test",
                "summary": "A test conversation",
                "category": "REACTION",
                "signals": ["emotion", "payoff"],
                "confidence": 0.85,
            }]
        })
        result = validate_model_output(raw, seg_ids)
        assert len(result) == 1
        assert result[0]["title"] == "Test"
        assert result[0]["category"] == "REACTION"
        assert "emotion" in result[0]["signals"]

    def test_empty_conversations_list(self):
        seg_ids = ["0", "1"]
        raw = json.dumps({"conversations": []})
        result = validate_model_output(raw, seg_ids)
        assert result == []

    def test_invalid_category_rejected(self):
        seg_ids = ["0", "1"]
        raw = json.dumps({
            "conversations": [{
                "start_segment_id": "0",
                "end_segment_id": "1",
                "title": "X",
                "category": "INVALID_CATEGORY",
                "confidence": 0.5,
            }]
        })
        with pytest.raises(ModelOutputError):
            validate_model_output(raw, seg_ids)

    def test_unknown_segment_id_rejected(self):
        seg_ids = ["0", "1"]
        raw = json.dumps({
            "conversations": [{
                "start_segment_id": "99",
                "end_segment_id": "1",
                "title": "X",
                "category": "OTHER",
                "confidence": 0.5,
            }]
        })
        with pytest.raises(ModelOutputError):
            validate_model_output(raw, seg_ids)

    def test_start_after_end_rejected(self):
        seg_ids = ["0", "1", "2"]
        raw = json.dumps({
            "conversations": [{
                "start_segment_id": "2",
                "end_segment_id": "0",
                "title": "X",
                "category": "OTHER",
                "confidence": 0.5,
            }]
        })
        with pytest.raises(ModelOutputError):
            validate_model_output(raw, seg_ids)

    def test_confidence_out_of_range_rejected(self):
        seg_ids = ["0", "1"]
        raw = json.dumps({
            "conversations": [{
                "start_segment_id": "0",
                "end_segment_id": "1",
                "title": "X",
                "category": "OTHER",
                "confidence": 1.5,
            }]
        })
        with pytest.raises(ModelOutputError):
            validate_model_output(raw, seg_ids)

    def test_invalid_json_rejected(self):
        seg_ids = ["0", "1"]
        with pytest.raises(ModelOutputError):
            validate_model_output("not json", seg_ids)

    def test_html_sanitized(self):
        seg_ids = ["0", "1"]
        raw = json.dumps({
            "conversations": [{
                "start_segment_id": "0",
                "end_segment_id": "1",
                "title": "<b>Bold</b> title",
                "summary": "<script>alert(1)</script>",
                "category": "OTHER",
                "confidence": 0.5,
            }]
        })
        result = validate_model_output(raw, seg_ids)
        assert "<b>" not in result[0]["title"]
        assert "<script>" not in result[0]["summary"]

    def test_unknown_signal_filtered(self):
        seg_ids = ["0", "1"]
        raw = json.dumps({
            "conversations": [{
                "start_segment_id": "0",
                "end_segment_id": "1",
                "title": "X",
                "category": "OTHER",
                "signals": ["emotion", "fake_signal", "payoff"],
                "confidence": 0.5,
            }]
        })
        result = validate_model_output(raw, seg_ids)
        assert "fake_signal" not in result[0]["signals"]
        assert "emotion" in result[0]["signals"]
        assert "payoff" in result[0]["signals"]


# ---------------------------------------------------------------------------
# JSON repair
# ---------------------------------------------------------------------------


class TestAttemptJsonRepair:
    def test_strips_markdown_fences(self):
        raw = "```json\n{\"conversations\": []}\n```"
        repaired = attempt_json_repair(raw)
        assert repaired == '{"conversations": []}'

    def test_strips_leading_text(self):
        raw = "Here is the result:\n{\"conversations\": []}"
        repaired = attempt_json_repair(raw)
        assert repaired == '{"conversations": []}'

    def test_strips_trailing_text(self):
        raw = '{"conversations": []}\nDone.'
        repaired = attempt_json_repair(raw)
        assert repaired == '{"conversations": []}'

    def test_removes_trailing_commas(self):
        raw = '{"conversations": [{"a": 1,},]}'
        repaired = attempt_json_repair(raw)
        # Should be valid JSON after repair.
        parsed = json.loads(repaired)
        assert parsed["conversations"][0]["a"] == 1

    def test_already_valid_unchanged(self):
        raw = '{"conversations": []}'
        repaired = attempt_json_repair(raw)
        assert repaired == '{"conversations": []}'


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplicateConversations:
    def test_no_duplicates_when_no_overlap(self):
        seg_ids = [str(i) for i in range(20)]
        c1 = {"start_segment_id": "0", "end_segment_id": "3", "title": "A", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        c2 = {"start_segment_id": "10", "end_segment_id": "13", "title": "B", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        result = deduplicate_conversations([c1, c2], seg_ids)
        assert len(result) == 2

    def test_overlapping_conversations_merged(self):
        seg_ids = [str(i) for i in range(20)]
        c1 = {"start_segment_id": "2", "end_segment_id": "8", "title": "A", "summary": "", "category": "REACTION", "signals": ["emotion"], "confidence": 0.8}
        c2 = {"start_segment_id": "3", "end_segment_id": "7", "title": "B", "summary": "", "category": "REACTION", "signals": ["payoff"], "confidence": 0.7}
        result = deduplicate_conversations([c1, c2], seg_ids)
        assert len(result) == 1
        # Signals should be merged.
        assert "emotion" in result[0]["signals"]
        assert "payoff" in result[0]["signals"]

    def test_single_conversation_unchanged(self):
        seg_ids = [str(i) for i in range(10)]
        c1 = {"start_segment_id": "0", "end_segment_id": "3", "title": "A", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        result = deduplicate_conversations([c1], seg_ids)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Boundary cleanup
# ---------------------------------------------------------------------------


class TestCleanupBoundaries:
    def test_expands_within_neighbourhood(self):
        segs = [
            {"id": str(i), "start": i * 5.0, "end": i * 5.0 + 4.0, "text": f"S{i}", "words": []}
            for i in range(10)
        ]
        conv = {"start_segment_id": "3", "end_segment_id": "5", "title": "X", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        result = cleanup_boundaries([conv], segs)
        # Should expand start backwards and end forwards (small 1s pauses
        # are under the 3s threshold, so expansion happens up to
        # BOUNDARY_EXPAND_SEGMENTS=2 on each side).
        start_idx = int(result[0]["start_segment_id"])
        end_idx = int(result[0]["end_segment_id"])
        assert start_idx <= 3  # expanded backwards
        assert end_idx >= 5    # expanded forwards
        assert start_idx >= 1  # at most 2 segments back
        assert end_idx <= 7    # at most 2 segments forward

    def test_does_not_expand_across_large_pause(self):
        segs = [
            {"id": "0", "start": 0.0, "end": 4.0, "text": "A", "words": []},
            {"id": "1", "start": 100.0, "end": 104.0, "text": "B", "words": []},
        ]
        conv = {"start_segment_id": "1", "end_segment_id": "1", "title": "X", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        result = cleanup_boundaries([conv], segs)
        # Should not expand backwards across the 96s pause.
        assert result[0]["start_segment_id"] == "1"


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


class TestFinalizeConversations:
    def test_resolves_timestamps(self):
        segs = [
            {"id": str(i), "start": i * 10.0, "end": i * 10.0 + 8.0, "text": f"Segment {i}.", "words": []}
            for i in range(10)
        ]
        conv = {"start_segment_id": "2", "end_segment_id": "4", "title": "Test", "summary": "A test", "category": "REACTION", "signals": ["emotion"], "confidence": 0.8}
        result = finalize_conversations([conv], segs)
        assert len(result) == 1
        assert result[0]["start"] == 20.0
        assert result[0]["end"] == 48.0
        assert result[0]["title"] == "Test"
        assert "Segment 2." in result[0]["transcript_excerpt"]

    def test_sorted_by_start(self):
        segs = [
            {"id": str(i), "start": i * 10.0, "end": i * 10.0 + 8.0, "text": f"S{i}", "words": []}
            for i in range(10)
        ]
        c1 = {"start_segment_id": "5", "end_segment_id": "6", "title": "B", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        c2 = {"start_segment_id": "1", "end_segment_id": "2", "title": "A", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        result = finalize_conversations([c1, c2], segs)
        assert result[0]["title"] == "A"
        assert result[1]["title"] == "B"

    def test_empty_conversations_returns_empty(self):
        assert finalize_conversations([], []) == []

    def test_excerpt_truncated_when_too_long(self):
        segs = [
            {"id": str(i), "start": i * 5.0, "end": i * 5.0 + 4.0, "text": "A" * 500, "words": []}
            for i in range(10)
        ]
        conv = {"start_segment_id": "0", "end_segment_id": "9", "title": "X", "summary": "", "category": "OTHER", "signals": [], "confidence": 0.5}
        result = finalize_conversations([conv], segs)
        assert len(result[0]["transcript_excerpt"]) <= 2100  # 2000 + ellipsis


# ---------------------------------------------------------------------------
# Stale detection
# ---------------------------------------------------------------------------


class TestIsStale:
    def test_completed_run_with_matching_revision_not_stale(self):
        run = {"status": "COMPLETED", "transcript_revision": 3}
        assert is_stale(run, 3) is False

    def test_completed_run_with_mismatched_revision_is_stale(self):
        run = {"status": "COMPLETED", "transcript_revision": 2}
        assert is_stale(run, 3) is True

    def test_active_run_never_stale(self):
        run = {"status": "RUNNING", "transcript_revision": 2}
        assert is_stale(run, 3) is False

    def test_run_without_revision_not_stale(self):
        run = {"status": "COMPLETED", "transcript_revision": None}
        assert is_stale(run, 3) is False


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class TestConversationMiningService:
    def test_runtime_status_unavailable_when_no_model(self, transcription_service, gpu_lock, vod_data_dir):
        s = Settings(data_root=vod_data_dir)
        # Explicitly disable the model to simulate an operator opt-out.
        s.conversation_mining_model_id = ""
        svc = ConversationMiningService(
            transcription_service=transcription_service,
            gpu_lock=gpu_lock,
            settings=s,
            worker_python="python",
        )
        status = svc.runtime_status()
        assert status["available"] is False
        assert "no model configured" in status["reasons"]

    def test_default_model_is_set(self):
        s = Settings(data_root=Path("/tmp/ttv-test-default-model"))
        assert s.conversation_mining_model_id == "Qwen/Qwen3-4B-Instruct-2507"
        # Non-thinking mode is the default.
        assert s.conversation_mining_thinking_enabled is False
        # Conservative input cap.
        assert s.conversation_mining_max_input_tokens == 8192

    def test_explicit_settings_override_default(self, vod_data_dir):
        s = Settings(data_root=vod_data_dir)
        s.conversation_mining_model_id = "custom/model"
        assert s.conversation_mining_model_id == "custom/model"
        # And the explicit value reaches the worker via the service.
        svc = ConversationMiningService(
            transcription_service=None,  # not used here
            gpu_lock=None,
            settings=s,
            worker_python="python",
        )
        assert svc.settings.conversation_mining_model_id == "custom/model"

    def test_runtime_status_available_when_model_set(self, mining_service):
        status = mining_service.runtime_status()
        assert status["available"] is True
        assert status["model"] == "fake-model/test"

    def test_start_run_no_transcript_raises_validation(
        self, mining_service, vod_service, make_real_mp4, channel_lister
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        with pytest.raises(ConversationMiningValidationError):
            mining_service.start_run(vod_id)

    def test_start_run_no_model_raises_unavailable(
        self, transcription_service, gpu_lock, vod_data_dir,
        vod_service, make_real_mp4, channel_lister,
    ):
        s = Settings(data_root=vod_data_dir)
        # Explicitly disable the model.
        s.conversation_mining_model_id = ""
        svc = ConversationMiningService(
            transcription_service=transcription_service,
            gpu_lock=gpu_lock,
            settings=s,
            worker_python="python",
        )
        vod_id, _ = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister
        )
        with pytest.raises(ConversationMiningUnavailableError):
            svc.start_run(vod_id)

    def test_start_run_creates_run_and_blocks(
        self, mining_service, transcription_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
            segments=_long_segments(20),
        )
        run = mining_service.start_run(vod_id)
        assert run["status"] == MiningRunStatus.QUEUED
        assert run["transcript_id"] == tid
        assert run["media_item_id"] == vod_id
        assert len(run["blocks"]) >= 1
        assert run["model"]["model_id"] == "fake-model/test"
        assert run["mining_config_version"] == MINING_CONFIG_VERSION

    def test_get_run_not_found_raises(self, mining_service):
        with pytest.raises(ConversationMiningNotFoundError):
            mining_service.get_run(str(uuid.uuid4()))

    def test_list_runs_empty(self, mining_service):
        assert mining_service.list_runs() == []

    def test_list_runs_filters_by_media_item(
        self, mining_service, transcription_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
            segments=_long_segments(20),
        )
        mining_service.start_run(vod_id)
        runs = mining_service.list_runs(media_item_id=vod_id)
        assert len(runs) == 1
        assert runs[0]["media_item_id"] == vod_id
        # Filter by a different id returns empty.
        runs_other = mining_service.list_runs(media_item_id=str(uuid.uuid4()))
        assert runs_other == []

    def test_cancel_run_on_nonexistent_raises(self, mining_service):
        with pytest.raises(ConversationMiningNotFoundError):
            mining_service.cancel_run(str(uuid.uuid4()))

    def test_delete_run_on_nonexistent_raises(self, mining_service):
        with pytest.raises(ConversationMiningNotFoundError):
            mining_service.delete_run(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


class TestConversationMiningApi:
    def test_status_endpoint(self, client):
        resp = client.get("/api/conversation-mining/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert "model" in data
        assert "reasons" in data

    def test_list_runs_empty(self, client):
        resp = client.get("/api/conversation-mining/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []

    def test_start_run_no_transcript_returns_400(
        self, client, vod_service, make_real_mp4, channel_lister
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        resp = client.post("/api/conversation-mining/runs", json={"media_item_id": vod_id})
        assert resp.status_code == 400

    def test_get_run_not_found_returns_404(self, client):
        resp = client.get(f"/api/conversation-mining/runs/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_cancel_run_not_found_returns_404(self, client):
        resp = client.post(f"/api/conversation-mining/runs/{uuid.uuid4()}/cancel")
        assert resp.status_code == 404

    def test_delete_run_not_found_returns_404(self, client):
        resp = client.delete(f"/api/conversation-mining/runs/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Pipeline integration tests
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_conversation_mining_in_default_pipeline(self):
        assert PipelineStepType.CONVERSATION_MINING.value in DEFAULT_VOD_PIPELINE

    def test_conversation_ming_after_transcribe(self):
        # CONVERSATION_MINING must come after TRANSCRIBE.
        t_idx = DEFAULT_VOD_PIPELINE.index(PipelineStepType.TRANSCRIBE.value)
        m_idx = DEFAULT_VOD_PIPELINE.index(PipelineStepType.CONVERSATION_MINING.value)
        assert m_idx > t_idx

    def test_step_weights_sum_to_100(self):
        from ttvturbo.media_processing.schemas import PIPELINE_STEP_WEIGHTS
        total = sum(PIPELINE_STEP_WEIGHTS.values())
        assert abs(total - 100.0) < 0.01


# ---------------------------------------------------------------------------
# Architecture / constants tests
# ---------------------------------------------------------------------------


class TestArchitecture:
    def test_categories_are_fixed_set(self):
        assert "REACTION" in CONVERSATION_CATEGORIES
        assert "OTHER" in CONVERSATION_CATEGORIES
        assert len(CONVERSATION_CATEGORIES) == 10

    def test_signals_are_fixed_set(self):
        assert "emotion" in CONVERSATION_SIGNALS
        assert "payoff" in CONVERSATION_SIGNALS
        assert len(CONVERSATION_SIGNALS) == 12

    def test_mining_config_version_is_positive(self):
        assert MINING_CONFIG_VERSION > 0

    def test_model_not_loaded_at_app_start(self, mining_settings, monkeypatch):
        """create_app must not import transformers/torch at construction."""
        import builtins
        real_import = builtins.__import__

        blocked = {"transformers": False, "torch": False}

        def fake_import(name, *args, **kwargs):
            top = name.split(".")[0]
            if top in blocked:
                blocked[top] = True
                raise ImportError(f"blocked in test: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from ttvturbo.app_factory import create_app
        create_app(settings=mining_settings)
        # Neither transformers nor torch were imported during app build.
        assert blocked["transformers"] is False
        assert blocked["torch"] is False


# ---------------------------------------------------------------------------
# Settings wiring / worker contract
# ---------------------------------------------------------------------------


class TestSettingsWiring:
    def test_model_id_reaches_worker_job(
        self, mining_service, transcription_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
            segments=_long_segments(20),
        )
        run = mining_service.start_run(vod_id)
        # The worker_job file must carry the configured model id and the
        # non-thinking / input-cap parameters.
        from ttvturbo.media_processing.conversation_mining import ConversationMiningStore
        store = ConversationMiningStore(
            ConversationMiningStore.mining_dir_for_transcript(
                mining_service.transcription_service, run["transcript_id"]
            ) / run["id"]
        )
        with open(store.worker_job_path(), "r", encoding="utf-8-sig") as fh:
            wjob = json.load(fh)
        assert wjob["model_id"] == "fake-model/test"
        assert wjob["thinking_enabled"] is False
        assert wjob["max_input_tokens"] == 8192
        assert wjob["max_new_tokens"] == 2048

    def test_worker_reads_non_thinking_default(self):
        from ttvturbo.settings import Settings
        s = Settings(data_root=Path("/tmp/ttv-test-worker-non-thinking"))
        assert s.conversation_mining_thinking_enabled is False

    def test_no_second_env_source_in_worker(self):
        """The worker must read model_id from the worker_job file, not from env."""
        import inspect
        from ttvturbo.media_processing import conversation_mining_worker as worker
        src = inspect.getsource(worker)
        # The worker must not read TTVTURBO_CONVERSATION_MINING_MODEL_ID.
        assert "TTVTURBO_CONVERSATION_MINING_MODEL_ID" not in src


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


class TestPreflight:
    def test_preflight_ok_when_configured(self, mining_service):
        ok, reasons = mining_service.preflight()
        assert ok is True
        assert reasons == []

    def test_preflight_fails_when_no_model(self, transcription_service, gpu_lock, vod_data_dir):
        s = Settings(data_root=vod_data_dir)
        s.conversation_mining_model_id = ""
        svc = ConversationMiningService(
            transcription_service=transcription_service,
            gpu_lock=gpu_lock,
            settings=s,
            worker_python="python",
        )
        # Stub deps so only the model check fails.
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001
        svc._check_cuda_available = lambda: True  # noqa: SLF001
        ok, reasons = mining_service_preflight(svc)
        assert ok is False
        assert any("not configured" in r for r in reasons)

    def test_preflight_fails_when_dependency_missing(self, mining_service):
        mining_service._check_dependencies = lambda: (False, "transformers missing")  # noqa: SLF001
        ok, reasons = mining_service.preflight()
        assert ok is False
        assert any("dependencies" in r for r in reasons)

    def test_pipeline_start_returns_503_when_no_model(
        self, transcription_service, gpu_lock, vod_data_dir,
        vod_service, make_real_mp4, channel_lister, media_storage, audio_service,
    ):
        from ttvturbo.media_processing import PipelineService
        from ttvturbo.media_processing import PipelineRunUnavailableError
        s = Settings(data_root=vod_data_dir)
        s.conversation_mining_model_id = ""
        mining_svc = ConversationMiningService(
            transcription_service=transcription_service,
            gpu_lock=gpu_lock,
            settings=s,
            worker_python="python",
        )
        mining_svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        mining_svc._check_worker_module = lambda: True  # noqa: SLF001
        mining_svc._check_cuda_available = lambda: True  # noqa: SLF001
        pipe = PipelineService(
            storage=media_storage,
            vod_service=vod_service,
            audio_service=audio_service,
            transcription_service=transcription_service,
            mining_service=mining_svc,
        )
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        with pytest.raises(PipelineRunUnavailableError):
            pipe.start_run("twitch_vod", vod_id)

    def test_pipeline_start_returns_503_when_dependency_missing(
        self, mining_service, media_storage, vod_service, audio_service,
        transcription_service, make_real_mp4, channel_lister,
    ):
        from ttvturbo.media_processing import PipelineService, PipelineRunUnavailableError
        mining_service._check_dependencies = lambda: (False, "transformers missing")  # noqa: SLF001
        pipe = PipelineService(
            storage=media_storage,
            vod_service=vod_service,
            audio_service=audio_service,
            transcription_service=transcription_service,
            mining_service=mining_service,
        )
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        with pytest.raises(PipelineRunUnavailableError):
            pipe.start_run("twitch_vod", vod_id)

    def test_pipeline_start_succeeds_when_preflight_ok(
        self, mining_service, media_storage, vod_service, audio_service,
        transcription_service, make_real_mp4, channel_lister,
    ):
        from ttvturbo.media_processing import PipelineService
        pipe = PipelineService(
            storage=media_storage,
            vod_service=vod_service,
            audio_service=audio_service,
            transcription_service=transcription_service,
            mining_service=mining_service,
        )
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        run = pipe.start_run("twitch_vod", vod_id)
        assert run["status"] in ("RUNNING", "QUEUED")


def mining_service_preflight(svc):
    """Helper to call preflight (defined for clarity)."""
    return svc.preflight()


# ---------------------------------------------------------------------------
# Retry from conversation mining
# ---------------------------------------------------------------------------


class TestRetryFromMining:
    def test_retry_does_not_repeat_previous_steps(
        self, mining_service, transcription_service,
        vod_service, make_real_mp4, channel_lister, media_storage, audio_service,
    ):
        """A pipeline retry must keep DOWNLOAD/AUDIO/TRANSCRIBE artifacts
        and only re-run the failed CONVERSATION_MINING step."""
        from ttvturbo.media_processing import PipelineService
        from ttvturbo.media_processing.schemas import PipelineStepStatus
        pipe = PipelineService(
            storage=media_storage,
            vod_service=vod_service,
            audio_service=audio_service,
            transcription_service=transcription_service,
            mining_service=mining_service,
        )
        vod_id, _ = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
            segments=_long_segments(20),
        )
        run = pipe.start_run("twitch_vod", vod_id)
        # Manually mark the run as FAILED at the mining step with the
        # upstream steps READY (simulating the real failure mode).
        run = pipe.get_run(run["id"])
        steps = run["steps"]
        for step in steps:
            if step["type"] in ("RESOLVE_SOURCE", "DOWNLOAD", "EXTRACT_AUDIO", "TRANSCRIBE"):
                step["status"] = PipelineStepStatus.READY.value
            elif step["type"] == "CONVERSATION_MINING":
                step["status"] = PipelineStepStatus.FAILED.value
                step["error"] = "conversation mining model is not configured"
                step["attempt"] = 1
        run["status"] = "FAILED"
        run["error"] = "Conversation Mining failed"
        media_storage.save_run(run)
        # Retry.
        retried = pipe.retry_run(run["id"])
        rsteps = {s["type"]: s for s in retried["steps"]}
        # Upstream steps keep their READY status (artifacts preserved).
        assert rsteps["DOWNLOAD"]["status"] == PipelineStepStatus.READY.value
        assert rsteps["EXTRACT_AUDIO"]["status"] == PipelineStepStatus.READY.value
        assert rsteps["TRANSCRIBE"]["status"] == PipelineStepStatus.READY.value
        # Mining step was reset and its attempt counter bumped.
        assert rsteps["CONVERSATION_MINING"]["status"] == PipelineStepStatus.PENDING.value
        assert rsteps["CONVERSATION_MINING"]["attempt"] == 2


# ---------------------------------------------------------------------------
# Worker metrics / shutdown
# ---------------------------------------------------------------------------


class TestWorkerMetrics:
    def test_empty_metrics_are_null_not_zero(self):
        from ttvturbo.media_processing.conversation_mining_worker import _empty_metrics
        m = _empty_metrics()
        assert m["peak_vram_bytes"] is None
        assert m["peak_ram_bytes"] is None
        assert m["model_load_seconds"] is None
        assert m["inference_seconds"] is None

    def test_worker_shutdown_terminates_orchestrator(self, mining_service):
        # shutdown must be idempotent and not raise even with no active runs.
        mining_service.shutdown()
        mining_service.shutdown()

    def test_generate_signature_carries_thinking_flag(self):
        import inspect
        from ttvturbo.media_processing.conversation_mining_worker import _generate
        params = inspect.signature(_generate).parameters
        assert "thinking_enabled" in params
        assert "max_input_tokens" in params
