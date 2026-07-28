"""Tests for editable transcript corrections (schema_version 2).

Covers:
* new transcript has immutable raw_text;
* segment correction save (single + batch);
* unchanged segments are not written;
* single + full reset;
* effective_text uses corrected text and falls back to raw;
* revision bump;
* revision conflict (HTTP 409);
* unknown segment id rejected;
* client cannot set raw_text / start / end;
* atomic write (no partial file on conflict);
* old schema_version 1 transcript stays readable;
* old transcript is migrated only on first save;
* revision history entries;
* no real user data (synthetic transcripts in tmp_path);
* test app uses a temporary data root.

These tests do not run the ASR worker. They write synthetic
schema_version 1 transcript artifacts directly and exercise the
correction layer on top.
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
    MediaJobNotFoundError,
    MediaJobValidationError,
    TranscriptionService,
    TranscriptRevisionConflictError,
    get_effective_segments,
    get_effective_text,
)
from ttvturbo.media_processing.transcription import (
    TRANSCRIPT_JSON,
    TRANSCRIPT_METADATA,
    TRANSCRIPTS_SUBDIR,
    ARTIFACTS_SUBDIR,
)
from ttvturbo.media_processing.transcript_corrections import (
    REVISIONS_JSON,
    TranscriptCorrectionStore,
    normalise_transcript,
)
from ttvturbo.media_processing_api import build_media_processing_router
from ttvturbo.storage_utils import atomic_write_json


# ---------------------------------------------------------------------------
# Local fixtures (mirror tests/test_media_processing.py — these are not in
# conftest because they are specific to the media-processing service stack).
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
    """Write a synthetic schema_version 1 transcript artifact (the shape
    the worker writes) and the matching metadata.json so the
    transcription service can discover it.

    Returns the transcription id.
    """
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
    atomic_write_json(tdir / TRANSCRIPT_JSON, payload, MediaJobValidationError, kind="transcript")
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
    atomic_write_json(tdir / TRANSCRIPT_METADATA, meta, MediaJobValidationError, kind="metadata")
    return tid


def _sample_segments() -> list[dict]:
    return [
        {"id": 0, "start": 1.42, "end": 3.85, "text": "Ich hab den Trick bekommen.", "words": []},
        {"id": 1, "start": 4.0, "end": 6.5, "text": "Das war echt cool.", "words": []},
    ]


def _make_ready_vod_with_transcript(
    transcription_service: TranscriptionService,
    vod_service,
    make_real_mp4,
    channel_lister,
    segments: list[dict] | None = None,
) -> tuple[str, str]:
    """Create a READY VOD with a synthetic transcript artifact.

    Returns (vod_id, transcription_id).
    """
    vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
    segs = segments if segments is not None else _sample_segments()
    tid = _write_v1_transcript(transcription_service, vod_id, segs)
    return vod_id, tid


def _make_ready_vod(vod_service, make_real_mp4, channel_lister, title: str = "Test VOD", login: str = "edittestcasepayt") -> tuple[str, Path]:
    """Create a VOD record with a READY status and a real MP4 file.

    Mirrors tests/test_media_processing._make_ready_vod but uses a
    distinct login so it never collides with other test modules.
    """
    from ttvturbo.vod_pipeline import VodStatus

    profile = vod_service.create_profile(login)
    profile_id = profile["id"]
    if not channel_lister.vods_by_login.get(login.lower()):
        channel_lister.add_vod(login, "700", title=title, duration=60.0)
    vod_service.sync_vods(profile_id)
    vods = vod_service.list_vods(profile_id=profile_id)
    assert vods, "sync_vods produced no VODs; check the channel_lister fixture"
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


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


class TestTranscriptCorrectionsService:
    def test_new_transcript_has_immutable_raw_text(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcript = transcription_service.get_transcript(tid)
        assert transcript["schema_version"] == 2
        assert transcript["revision"] == 1
        assert transcript["correction_status"] == "RAW"
        assert transcript["raw_text"] == "Ich hab den Trick bekommen. Das war echt cool."
        assert transcript["corrected_text"] is None
        for seg in transcript["segments"]:
            assert seg["raw_text"] is not None
            assert seg["corrected_text"] is None
            assert isinstance(seg["id"], str)
            assert seg["id"].startswith("segment-")

    def test_save_single_segment_correction(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        updated = transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Ich hab den Drake bekommen."}],
        )
        assert updated["revision"] == 2
        assert updated["correction_status"] == "CORRECTED"
        assert updated["segments"][0]["corrected_text"] == "Ich hab den Drake bekommen."
        assert updated["segments"][0]["raw_text"] == "Ich hab den Trick bekommen."
        assert updated["corrected_text"] == "Ich hab den Drake bekommen. Das war echt cool."

    def test_save_batch_segments(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        updated = transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[
                {"segment_id": "segment-0", "corrected_text": "Ich hab den Drake bekommen."},
                {"segment_id": "segment-1", "corrected_text": "Das war echt stark."},
            ],
        )
        assert updated["revision"] == 2
        assert updated["segments"][0]["corrected_text"] == "Ich hab den Drake bekommen."
        assert updated["segments"][1]["corrected_text"] == "Das war echt stark."
        assert updated["corrected_text"] == "Ich hab den Drake bekommen. Das war echt stark."

    def test_unchanged_segments_not_written(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        # First save to bump to revision 2.
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        # Second save with a no-op for segment-0 (same value) and no
        # other changes — revision must NOT bump.
        before = transcription_service.get_transcript(tid)
        updated = transcription_service.save_corrections(
            tid, expected_revision=before["revision"],
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        assert updated["revision"] == before["revision"]
        revisions = transcription_service.list_revisions(tid)
        assert len(revisions) == 1  # only the first save recorded

    def test_reset_single_segment(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        reset = transcription_service.reset_segment_correction(tid, "segment-0")
        assert reset["segments"][0]["corrected_text"] is None
        assert reset["segments"][0]["raw_text"] == "Ich hab den Trick bekommen."
        assert reset["correction_status"] == "RAW"
        assert reset["revision"] == 3

    def test_reset_all_corrections(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[
                {"segment_id": "segment-0", "corrected_text": "Drake"},
                {"segment_id": "segment-1", "corrected_text": "Stark"},
            ],
        )
        reset = transcription_service.reset_all_corrections(tid)
        assert reset["correction_status"] == "RAW"
        assert all(s["corrected_text"] is None for s in reset["segments"])
        assert reset["revision"] == 3
        # raw_text preserved
        assert reset["raw_text"] == "Ich hab den Trick bekommen. Das war echt cool."

    def test_effective_text_uses_corrected(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        transcript = transcription_service.get_transcript(tid)
        assert get_effective_text(transcript) == "Drake Das war echt cool."
        segs = get_effective_segments(transcript)
        assert segs[0]["text"] == "Drake"
        assert segs[1]["text"] == "Das war echt cool."

    def test_effective_text_falls_back_to_raw(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcript = transcription_service.get_transcript(tid)
        assert get_effective_text(transcript) == "Ich hab den Trick bekommen. Das war echt cool."

    def test_revision_increases(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        r1 = transcription_service.get_transcript(tid)["revision"]
        t2 = transcription_service.save_corrections(
            tid, expected_revision=r1,
            segments=[{"segment_id": "segment-0", "corrected_text": "A"}],
        )
        t3 = transcription_service.save_corrections(
            tid, expected_revision=t2["revision"],
            segments=[{"segment_id": "segment-1", "corrected_text": "B"}],
        )
        assert t3["revision"] == r1 + 2

    def test_revision_conflict_raises(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "A"}],
        )
        with pytest.raises(TranscriptRevisionConflictError) as ei:
            transcription_service.save_corrections(
                tid, expected_revision=1,  # stale
                segments=[{"segment_id": "segment-1", "corrected_text": "B"}],
            )
        assert ei.value.current_revision == 2
        # The file must not have been modified by the conflicting call.
        transcript = transcription_service.get_transcript(tid)
        assert transcript["segments"][1]["corrected_text"] is None

    def test_unknown_segment_id_rejected(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        with pytest.raises(MediaJobValidationError):
            transcription_service.save_corrections(
                tid, expected_revision=1,
                segments=[{"segment_id": "segment-999", "corrected_text": "X"}],
            )

    def test_client_cannot_set_raw_text(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        with pytest.raises(MediaJobValidationError):
            transcription_service.save_corrections(
                tid, expected_revision=1,
                segments=[{"segment_id": "segment-0", "raw_text": "hacked", "corrected_text": "X"}],
            )
        with pytest.raises(MediaJobValidationError):
            transcription_service.save_corrections(
                tid, expected_revision=1,
                segments=[{"segment_id": "segment-0", "start": 0.0, "corrected_text": "X"}],
            )

    def test_atomic_write_no_partial_on_conflict(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        tdir = transcription_service._find_transcription_dir(tid)
        transcript_path = tdir / TRANSCRIPT_JSON
        original = transcript_path.read_text(encoding="utf-8")
        with pytest.raises(TranscriptRevisionConflictError):
            transcription_service.save_corrections(
                tid, expected_revision=999,
                segments=[{"segment_id": "segment-0", "corrected_text": "X"}],
            )
        # The file must be byte-for-byte intact (atomic write, no partial).
        assert transcript_path.read_text(encoding="utf-8") == original

    def test_old_transcript_stays_readable(self, transcription_service, vod_service, make_real_mp4, channel_lister):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        # The on-disk file is still schema_version 1 (we did not save yet).
        tdir = transcription_service._find_transcription_dir(tid)
        raw = json.loads((tdir / TRANSCRIPT_JSON).read_text(encoding="utf-8"))
        assert raw["schema_version"] == 1
        # But reading through the service yields a v2 view.
        transcript = transcription_service.get_transcript(tid)
        assert transcript["schema_version"] == 2
        assert transcript["revision"] == 1

    def test_old_transcript_migrated_only_on_save(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        tdir = transcription_service._find_transcription_dir(tid)
        transcript_path = tdir / TRANSCRIPT_JSON
        # Before save: still v1 on disk.
        assert json.loads(transcript_path.read_text(encoding="utf-8"))["schema_version"] == 1
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        # After save: migrated to v2 on disk.
        raw = json.loads(transcript_path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 2
        assert raw["revision"] == 2
        assert raw["segments"][0]["raw_text"] == "Ich hab den Trick bekommen."
        assert raw["segments"][0]["corrected_text"] == "Drake"
        # The original `text` field is gone (replaced by raw_text).
        assert "text" not in raw["segments"][0]

    def test_revision_history_recorded(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        revisions = transcription_service.list_revisions(tid)
        assert len(revisions) == 1
        entry = revisions[0]
        assert entry["revision"] == 2
        assert entry["changes"][0]["segment_id"] == "segment-0"
        assert entry["changes"][0]["before"] is None
        assert entry["changes"][0]["after"] == "Drake"
        # A second save appends.
        transcription_service.save_corrections(
            tid, expected_revision=2,
            segments=[{"segment_id": "segment-1", "corrected_text": "Stark"}],
        )
        revisions = transcription_service.list_revisions(tid)
        assert len(revisions) == 2
        assert revisions[1]["revision"] == 3

    def test_empty_correction_treated_as_null(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        # Reset via empty string.
        updated = transcription_service.save_corrections(
            tid, expected_revision=2,
            segments=[{"segment_id": "segment-0", "corrected_text": "   "}],
        )
        assert updated["segments"][0]["corrected_text"] is None
        assert updated["correction_status"] == "RAW"

    def test_whitespace_normalised(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        updated = transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "  Ich   hab   den   Drake  "}],
        )
        assert updated["segments"][0]["corrected_text"] == "Ich hab den Drake"

    def test_effective_contract_for_consumers(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        transcription_service.save_corrections(
            tid, expected_revision=1,
            segments=[{"segment_id": "segment-0", "corrected_text": "Drake"}],
        )
        contract = transcription_service.get_transcript_contract(tid)
        assert contract["transcript_id"] == tid
        assert contract["revision"] == 2
        assert contract["effective_text"] == "Drake Das war echt cool."
        assert contract["effective_segments"][0]["text"] == "Drake"

    def test_invalid_transcription_id_rejected(self, transcription_service):
        with pytest.raises(MediaJobValidationError):
            transcription_service.get_transcript("not-a-uuid")
        with pytest.raises(MediaJobValidationError):
            transcription_service.save_corrections("not-a-uuid", 1, [])


# ---------------------------------------------------------------------------
# API-level tests (HTTP)
# ---------------------------------------------------------------------------


def _build_api_client(transcription_service: TranscriptionService) -> TestClient:
    app = FastAPI()
    router = build_media_processing_router(
        audio_service=transcription_service.audio_service,
        transcription_service=transcription_service,
        pipeline_service=None,  # type: ignore[arg-type]
    )
    app.include_router(router)
    return TestClient(app)


class TestTranscriptCorrectionsAPI:
    def test_get_transcript_view(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        client = _build_api_client(transcription_service)
        r = client.get(f"/api/transcriptions/{tid}/transcript")
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == 2
        assert body["revision"] == 1
        assert body["raw_text"] == "Ich hab den Trick bekommen. Das war echt cool."

    def test_patch_corrections(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        client = _build_api_client(transcription_service)
        r = client.patch(
            f"/api/transcriptions/{tid}/corrections",
            json={
                "expected_revision": 1,
                "segments": [
                    {"segment_id": "segment-0", "corrected_text": "Ich hab den Drake bekommen."},
                    {"segment_id": "segment-1", "corrected_text": None},
                ],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["revision"] == 2
        assert body["segments"][0]["corrected_text"] == "Ich hab den Drake bekommen."

    def test_revision_conflict_http_409(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        client = _build_api_client(transcription_service)
        # First save succeeds (revision -> 2).
        r1 = client.patch(
            f"/api/transcriptions/{tid}/corrections",
            json={"expected_revision": 1, "segments": [
                {"segment_id": "segment-0", "corrected_text": "A"}]},
        )
        assert r1.status_code == 200
        # Stale revision -> 409 with current revision in body.
        r2 = client.patch(
            f"/api/transcriptions/{tid}/corrections",
            json={"expected_revision": 1, "segments": [
                {"segment_id": "segment-1", "corrected_text": "B"}]},
        )
        assert r2.status_code == 409
        detail = r2.json()["detail"]
        assert detail["code"] == "revision_conflict"
        assert detail["current_revision"] == 2
        assert detail["transcript"]["revision"] == 2

    def test_reset_segment_endpoint(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        client = _build_api_client(transcription_service)
        client.patch(
            f"/api/transcriptions/{tid}/corrections",
            json={"expected_revision": 1, "segments": [
                {"segment_id": "segment-0", "corrected_text": "Drake"}]},
        )
        r = client.post(f"/api/transcriptions/{tid}/segments/segment-0/reset")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["segments"][0]["corrected_text"] is None

    def test_reset_all_endpoint(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        client = _build_api_client(transcription_service)
        client.patch(
            f"/api/transcriptions/{tid}/corrections",
            json={"expected_revision": 1, "segments": [
                {"segment_id": "segment-0", "corrected_text": "Drake"},
                {"segment_id": "segment-1", "corrected_text": "Stark"},
            ]},
        )
        r = client.post(f"/api/transcriptions/{tid}/reset-corrections")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["correction_status"] == "RAW"
        assert all(s["corrected_text"] is None for s in body["segments"])

    def test_revisions_endpoint(
        self, transcription_service, vod_service, make_real_mp4, channel_lister,
    ):
        _, tid = _make_ready_vod_with_transcript(
            transcription_service, vod_service, make_real_mp4, channel_lister,
        )
        client = _build_api_client(transcription_service)
        client.patch(
            f"/api/transcriptions/{tid}/corrections",
            json={"expected_revision": 1, "segments": [
                {"segment_id": "segment-0", "corrected_text": "Drake"}]},
        )
        r = client.get(f"/api/transcriptions/{tid}/revisions")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["revisions"]) == 1
        assert body["revisions"][0]["revision"] == 2

    def test_unknown_transcript_404(self, transcription_service):
        client = _build_api_client(transcription_service)
        r = client.get(f"/api/transcriptions/{uuid.uuid4()}/transcript")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Unit tests for the pure helpers
# ---------------------------------------------------------------------------


class TestTranscriptCorrectionHelpers:
    def test_normalise_v1_transcript(self):
        raw = {
            "schema_version": 1,
            "id": "abc",
            "source_id": "vod-1",
            "model": "large-v3",
            "language": "de",
            "created_at": "2024-01-01T00:00:00+00:00",
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": "Hallo"},
                {"id": 1, "start": 1.0, "end": 2.0, "text": "Welt"},
            ],
        }
        norm = normalise_transcript(raw, job={"options": {"model_family": "parakeet"}})
        assert norm["schema_version"] == 2
        assert norm["revision"] == 1
        assert norm["raw_text"] == "Hallo Welt"
        assert norm["corrected_text"] is None
        assert norm["segments"][0]["id"] == "segment-0"
        assert norm["segments"][0]["raw_text"] == "Hallo"
        assert norm["engine"]["family"] == "parakeet"
        assert norm["engine"]["model"] == "large-v3"

    def test_normalise_v2_transcript_preserves_corrections(self):
        raw = {
            "schema_version": 2,
            "id": "abc",
            "source_id": "vod-1",
            "revision": 3,
            "raw_text": "Hallo Welt",
            "corrected_text": "Hallo Wält",
            "correction_status": "CORRECTED",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-02T00:00:00+00:00",
            "segments": [
                {"id": "segment-0", "start": 0.0, "end": 1.0, "raw_text": "Hallo", "corrected_text": None},
                {"id": "segment-1", "start": 1.0, "end": 2.0, "raw_text": "Welt", "corrected_text": "Wält"},
            ],
        }
        norm = normalise_transcript(raw)
        assert norm["revision"] == 3
        assert norm["correction_status"] == "CORRECTED"
        assert norm["corrected_text"] == "Hallo Wält"
        assert norm["segments"][1]["corrected_text"] == "Wält"

    def test_get_effective_text_fallback(self):
        raw = {"schema_version": 1, "segments": [{"id": 0, "start": 0, "end": 1, "text": "Foo"}]}
        assert get_effective_text(raw) == "Foo"

    def test_get_effective_text_corrected(self):
        raw = {
            "schema_version": 2,
            "raw_text": "Foo",
            "corrected_text": "Bar",
            "segments": [{"id": "segment-0", "start": 0, "end": 1, "raw_text": "Foo", "corrected_text": "Bar"}],
        }
        assert get_effective_text(raw) == "Bar"

    def test_store_save_migrates_and_writes_revisions_sidecar(
        self, tmp_path: Path,
    ):
        tdir = tmp_path / "transcript"
        tdir.mkdir(parents=True)
        raw = {
            "schema_version": 1,
            "id": "t1",
            "source_id": "v1",
            "model": "large-v3",
            "language": "de",
            "created_at": "2024-01-01T00:00:00+00:00",
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "Raw"}],
        }
        atomic_write_json(tdir / TRANSCRIPT_JSON, raw, MediaJobValidationError, kind="transcript")
        store = TranscriptCorrectionStore(tdir)
        updated = store.save_corrections(
            expected_revision=1,
            segment_updates=[{"segment_id": "segment-0", "corrected_text": "Corrected"}],
        )
        assert updated["revision"] == 2
        assert (tdir / REVISIONS_JSON).is_file()
        revs = store.list_revisions()
        assert len(revs) == 1
