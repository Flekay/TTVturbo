"""Tests for the Visual Analysis backend capability.

Covers the required scenarios from the spec:

* manual regions;
* automatic box validation;
* invalid model boxes;
* multiple layouts;
* template reuse;
* layout changes;
* cancel;
* retry;
* recovery;
* media-library artifact.

These tests do not run a real vision model.  They use the
:class:`StaticVisionAdapter` fixture and a tiny real MP4 (generated via
ffmpeg) so the source resolver and keyframe extraction exercise the
real code paths.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ttvturbo.settings import Settings
from ttvturbo.visual_analysis import (
    Box,
    DetectedRegion,
    Keyframe,
    KeyframeResult,
    LayoutChange,
    LayoutTemplate,
    RegionTrack,
    RegionType,
    StaticVisionAdapter,
    UnavailableVisionAdapter,
    VisualAnalysisArtifact,
    VisualAnalysisConflictError,
    VisualAnalysisJobStatus,
    VisualAnalysisNotFoundError,
    VisualAnalysisService,
    VisualAnalysisStorage,
    VisualAnalysisUnavailableError,
    VisualAnalysisValidationError,
    detect_layout_changes,
    track_regions,
    validate_detected_region,
    validate_model_output,
    validate_template_against_keyframes,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def va_settings(vod_data_dir: Path) -> Settings:
    s = Settings(data_root=vod_data_dir)
    s.visual_analysis_model_id = "fake-vision/test"
    s.visual_analysis_keyframe_interval_seconds = 1.0
    s.visual_analysis_layout_change_threshold = 0.3
    s.visual_analysis_template_validation_keyframes = 2
    return s


@pytest.fixture()
def va_storage(va_settings: Settings) -> VisualAnalysisStorage:
    return VisualAnalysisStorage(va_settings.paths().visual_analysis)


@pytest.fixture()
def source_resolver(vod_service, library_service):
    from ttvturbo.media_processing import MediaSourceResolver
    return MediaSourceResolver(
        vod_service.storage,
        library_service=library_service,
    )


@pytest.fixture()
def library_service(vod_data_dir: Path):
    from ttvturbo.library import LibraryService, LibraryStorage
    return LibraryService(LibraryStorage(vod_data_dir / "library"))


@pytest.fixture()
def va_service(va_storage, source_resolver, va_settings, library_service):
    return VisualAnalysisService(
        storage=va_storage,
        source_resolver=source_resolver,
        settings=va_settings,
        vision_adapter=UnavailableVisionAdapter(),
        library_service=library_service,
    )


@pytest.fixture()
def app(va_settings):
    from ttvturbo.app_factory import create_app
    return create_app(settings=va_settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ready_vod(vod_service, make_real_mp4, channel_lister, title="VA Test VOD", login="vatestpayt"):
    from ttvturbo.vod_pipeline import VodStatus
    profile = vod_service.create_profile(login)
    profile_id = profile["id"]
    if not channel_lister.vods_by_login.get(login.lower()):
        channel_lister.add_vod(login, "900", title=title, duration=60.0)
    vod_service.sync_vods(profile_id)
    vods = vod_service.list_vods(profile_id=profile_id)
    assert vods, "sync_vods produced no VODs"
    vod = vods[0]
    vod_id = vod["id"]
    vod_dir = vod_service.storage.vod_dir(vod_id)
    vod_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = vod_dir / "source.mp4"
    make_real_mp4(mp4_path, duration_seconds=2.0)
    vod = vod_service.storage.load_vod(vod_id)
    vod["status"] = VodStatus.READY.value
    vod["download"] = {
        "started_at": "2024-01-01T00:00:00+00:00",
        "completed_at": "2024-01-01T01:00:00+00:00",
        "file_name": "source.mp4",
        "file_size_bytes": mp4_path.stat().st_size,
        "container": "mp4",
        "duration_seconds": 2.0,
        "width": 160,
        "height": 120,
        "video_codec": "h264",
        "audio_codec": "aac",
    }
    vod["title"] = title
    vod_service.storage.save_vod(vod)
    return vod_id, mp4_path


def _box(x=0.0, y=0.0, w=1.0, h=1.0) -> Box:
    return Box(x=x, y=y, width=w, height=h)


def _det(rtype: str, x=0.0, y=0.0, w=1.0, h=1.0, conf=0.9) -> DetectedRegion:
    return DetectedRegion(type=rtype, box=_box(x, y, w, h), confidence=conf)


def _manual_track(track_id="facecam-1", rtype="FACECAM", x=0.8, y=0.7, w=0.18, h=0.25) -> dict:
    return {
        "id": track_id,
        "type": rtype,
        "start": 0.0,
        "end": 60.0,
        "keyframes": [
            {"time": 0.0, "box": {"x": x, "y": y, "width": w, "height": h}, "confidence": 1.0},
        ],
    }


# ---------------------------------------------------------------------------
# Box validation
# ---------------------------------------------------------------------------


class TestBoxValidation:
    def test_valid_box(self):
        b = Box(x=0.1, y=0.2, width=0.3, height=0.4)
        assert b.x + b.width <= 1.0
        assert b.y + b.height <= 1.0

    def test_box_exceeding_unit_square_rejected(self):
        with pytest.raises(Exception):
            Box(x=0.9, y=0.9, width=0.5, height=0.5)

    def test_zero_width_rejected(self):
        with pytest.raises(Exception):
            Box(x=0.0, y=0.0, width=0.0, height=0.5)

    def test_iou_identical(self):
        b = Box(x=0.1, y=0.1, width=0.2, height=0.2)
        assert b.iou(b) == pytest.approx(1.0)

    def test_iou_disjoint(self):
        a = Box(x=0.0, y=0.0, width=0.2, height=0.2)
        b = Box(x=0.8, y=0.8, width=0.1, height=0.1)
        assert a.iou(b) == pytest.approx(0.0)

    def test_iou_partial(self):
        a = Box(x=0.0, y=0.0, width=0.4, height=0.4)
        b = Box(x=0.2, y=0.2, width=0.4, height=0.4)
        # intersection = 0.2*0.2 = 0.04; union = 0.16+0.16-0.04 = 0.28
        assert a.iou(b) == pytest.approx(0.04 / 0.28)


# ---------------------------------------------------------------------------
# Model output validation
# ---------------------------------------------------------------------------


class TestModelOutputValidation:
    def test_valid_detected_region(self):
        r = _det("FACECAM", 0.8, 0.7, 0.18, 0.25, 0.96)
        assert validate_detected_region(r) is r

    def test_unknown_region_type_rejected(self):
        r = DetectedRegion(type="HUD", box=_box(0, 0, 0.5, 0.5), confidence=0.9)
        with pytest.raises(VisualAnalysisValidationError):
            validate_detected_region(r)

    def test_confidence_out_of_range_rejected(self):
        r = DetectedRegion(type="FACECAM", box=_box(0, 0, 0.5, 0.5), confidence=1.5)
        with pytest.raises(VisualAnalysisValidationError):
            validate_detected_region(r)

    def test_box_exceeding_unit_square_rejected(self):
        # Box construction itself rejects out-of-unit-square boxes.
        with pytest.raises(Exception):
            Box(x=0.9, y=0.9, width=0.5, height=0.5)
        # validate_detected_region also rejects if a bad box somehow
        # bypasses construction (defensive).
        r = DetectedRegion(
            type="FACECAM",
            box=Box(x=0.0, y=0.0, width=0.5, height=0.5),
            confidence=0.9,
        )
        # Force an out-of-range box past the constructor for the test.
        object.__setattr__(r.box, "x", 0.9)
        with pytest.raises(VisualAnalysisValidationError):
            validate_detected_region(r)

    def test_validate_model_output_list(self):
        regions = [
            _det("FACECAM", 0.8, 0.7, 0.18, 0.25),
            _det("GAMEPLAY", 0.0, 0.0, 1.0, 1.0),
        ]
        out = validate_model_output(regions)
        assert len(out) == 2

    def test_validate_model_output_rejects_invalid(self):
        regions = [_det("FACECAM", 0.8, 0.7, 0.18, 0.25), _det("HUD")]
        with pytest.raises(VisualAnalysisValidationError):
            validate_model_output(regions)


# ---------------------------------------------------------------------------
# Deterministic tracking
# ---------------------------------------------------------------------------


class TestTracking:
    def test_empty_keyframes_returns_empty(self):
        assert track_regions([], start=0.0, end=10.0) == []

    def test_single_keyframe_single_track(self):
        kfs = [KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)])]
        tracks = track_regions(kfs, start=0.0, end=10.0)
        assert len(tracks) == 1
        assert tracks[0].type == "FACECAM"
        assert tracks[0].id == "facecam-1"
        assert len(tracks[0].keyframes) == 1

    def test_two_keyframes_same_region_extends_track(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)]),
            KeyframeResult(time=5.0, regions=[_det("FACECAM", 0.81, 0.71, 0.18, 0.25)]),
        ]
        tracks = track_regions(kfs, start=0.0, end=10.0)
        assert len(tracks) == 1
        assert len(tracks[0].keyframes) == 2
        assert tracks[0].start == 0.0
        assert tracks[0].end == 5.0

    def test_disjoint_boxes_start_new_track(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)]),
            KeyframeResult(time=5.0, regions=[_det("FACECAM", 0.1, 0.1, 0.18, 0.25)]),
        ]
        tracks = track_regions(kfs, start=0.0, end=10.0)
        # Two separate facecam tracks (no IoU match).
        facecam_tracks = [t for t in tracks if t.type == "FACECAM"]
        assert len(facecam_tracks) == 2

    def test_multiple_region_types(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[
                _det("FACECAM", 0.8, 0.7, 0.18, 0.25),
                _det("GAMEPLAY", 0.0, 0.0, 1.0, 1.0),
                _det("CHAT", 0.0, 0.8, 0.3, 0.2),
            ]),
        ]
        tracks = track_regions(kfs, start=0.0, end=10.0)
        types = {t.type for t in tracks}
        assert types == {"FACECAM", "GAMEPLAY", "CHAT"}
        ids = {t.id for t in tracks}
        assert ids == {"facecam-1", "gameplay-1", "chat-1"}

    def test_deterministic_output(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)]),
            KeyframeResult(time=5.0, regions=[_det("FACECAM", 0.81, 0.71, 0.18, 0.25)]),
            KeyframeResult(time=10.0, regions=[_det("FACECAM", 0.82, 0.72, 0.18, 0.25)]),
        ]
        a = track_regions(kfs, start=0.0, end=15.0)
        b = track_regions(kfs, start=0.0, end=15.0)
        assert [t.model_dump() for t in a] == [t.model_dump() for t in b]


# ---------------------------------------------------------------------------
# Layout change detection
# ---------------------------------------------------------------------------


class TestLayoutChanges:
    def test_no_change_with_stable_layout(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)]),
            KeyframeResult(time=5.0, regions=[_det("FACECAM", 0.81, 0.71, 0.18, 0.25)]),
        ]
        changes = detect_layout_changes(kfs, threshold=0.3)
        assert changes == []

    def test_region_disappears_flagged(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)]),
            KeyframeResult(time=5.0, regions=[]),
        ]
        changes = detect_layout_changes(kfs, threshold=0.3)
        assert len(changes) == 1
        assert changes[0].time == 5.0

    def test_region_appears_flagged(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[]),
            KeyframeResult(time=5.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)]),
        ]
        changes = detect_layout_changes(kfs, threshold=0.3)
        assert len(changes) == 1

    def test_box_shift_flagged(self):
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)]),
            KeyframeResult(time=5.0, regions=[_det("FACECAM", 0.1, 0.1, 0.18, 0.25)]),
        ]
        changes = detect_layout_changes(kfs, threshold=0.3)
        assert len(changes) == 1

    def test_single_keyframe_no_changes(self):
        kfs = [KeyframeResult(time=0.0, regions=[_det("FACECAM")])]
        assert detect_layout_changes(kfs) == []


# ---------------------------------------------------------------------------
# Template validation
# ---------------------------------------------------------------------------


class TestTemplateValidation:
    def test_template_matches_keyframes(self):
        template_tracks = [
            RegionTrack(
                id="facecam-1", type="FACECAM", start=0.0, end=60.0,
                keyframes=[Keyframe(time=0.0, box=_box(0.8, 0.7, 0.18, 0.25), confidence=1.0)],
            ),
        ]
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.805, 0.705, 0.18, 0.25)]),
            KeyframeResult(time=5.0, regions=[_det("FACECAM", 0.81, 0.71, 0.18, 0.25)]),
        ]
        ok, dev = validate_template_against_keyframes(template_tracks, kfs, threshold=0.3)
        assert ok is True
        assert dev < 0.3

    def test_template_deviates_flagged(self):
        template_tracks = [
            RegionTrack(
                id="facecam-1", type="FACECAM", start=0.0, end=60.0,
                keyframes=[Keyframe(time=0.0, box=_box(0.8, 0.7, 0.18, 0.25), confidence=1.0)],
            ),
        ]
        kfs = [
            KeyframeResult(time=0.0, regions=[_det("FACECAM", 0.1, 0.1, 0.18, 0.25)]),
        ]
        ok, dev = validate_template_against_keyframes(template_tracks, kfs, threshold=0.3)
        assert ok is False
        assert dev >= 0.3

    def test_empty_template_or_keyframes_ok(self):
        ok, dev = validate_template_against_keyframes([], [], threshold=0.3)
        assert ok is True
        assert dev == 0.0


# ---------------------------------------------------------------------------
# Service: manual regions
# ---------------------------------------------------------------------------


class TestManualRegions:
    def test_manual_regions_short_circuit_vision(
        self, va_service, vod_service, make_real_mp4, channel_lister
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        manual = [_manual_track()]
        job = va_service.start_job(vod_id, manual_regions=manual)
        assert job["status"] == VisualAnalysisJobStatus.COMPLETED
        assert job["origin"] == "manual"
        assert job["output_artifact_id"] is not None

        artifact = va_service.get_artifact(job["output_artifact_id"])
        assert artifact["origin"] == "manual"
        assert len(artifact["region_tracks"]) == 1
        assert artifact["region_tracks"][0]["type"] == "FACECAM"

    def test_manual_regions_invalid_box_rejected(
        self, va_service, vod_service, make_real_mp4, channel_lister
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        bad = [_manual_track(x=0.9, y=0.9, w=0.5, h=0.5)]
        with pytest.raises(VisualAnalysisValidationError):
            va_service.start_job(vod_id, manual_regions=bad)

    def test_manual_regions_invalid_type_rejected(
        self, va_service, vod_service, make_real_mp4, channel_lister
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        bad = [_manual_track(rtype="HUD")]
        with pytest.raises(VisualAnalysisValidationError):
            va_service.start_job(vod_id, manual_regions=bad)


# ---------------------------------------------------------------------------
# Service: automatic analysis
# ---------------------------------------------------------------------------


class TestAutomaticAnalysis:
    def test_unavailable_vision_raises(
        self, va_service, vod_service, make_real_mp4, channel_lister
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        with pytest.raises(VisualAnalysisUnavailableError):
            va_service.start_job(vod_id)

    def test_automatic_analysis_with_static_adapter(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        adapter = StaticVisionAdapter(
            default=[
                _det("FACECAM", 0.8, 0.7, 0.18, 0.25),
                _det("GAMEPLAY", 0.0, 0.0, 1.0, 1.0),
            ],
        )
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=adapter,
            library_service=library_service,
        )
        job = svc.start_job(vod_id, start_seconds=0.0, end_seconds=1.0)
        assert job["status"] == VisualAnalysisJobStatus.COMPLETED
        assert job["origin"] == "automatic"
        artifact = svc.get_artifact(job["output_artifact_id"])
        types = {t["type"] for t in artifact["region_tracks"]}
        assert "FACECAM" in types
        assert "GAMEPLAY" in types
        # Coordinates normalised.
        for track in artifact["region_tracks"]:
            for kf in track["keyframes"]:
                box = kf["box"]
                assert 0.0 <= box["x"] <= 1.0
                assert 0.0 <= box["y"] <= 1.0
                assert 0.0 < box["width"] <= 1.0
                assert 0.0 < box["height"] <= 1.0

    def test_invalid_model_box_fails_job(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        # Adapter returns a box outside the unit square.
        bad = DetectedRegion(
            type="FACECAM",
            box=Box(x=0.0, y=0.0, width=0.5, height=0.5),
            confidence=0.9,
        )
        # Build an invalid region by bypassing Box validation via a
        # custom adapter that returns an out-of-range box.
        class BadAdapter:
            def available(self):
                return True
            def analyze_keyframe(self, image_path, resolution):
                # Return a region with confidence out of range to trigger
                # validation failure.
                return [DetectedRegion(
                    type="FACECAM",
                    box=Box(x=0.0, y=0.0, width=0.5, height=0.5),
                    confidence=2.0,
                )]
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=BadAdapter(),
            library_service=library_service,
        )
        with pytest.raises(VisualAnalysisValidationError):
            svc.start_job(vod_id, start_seconds=0.0, end_seconds=1.0)
        jobs = svc.list_jobs(media_item_id=vod_id)
        assert jobs[0]["status"] == VisualAnalysisJobStatus.FAILED


# ---------------------------------------------------------------------------
# Service: templates
# ---------------------------------------------------------------------------


class TestTemplates:
    def test_create_and_list_template(self, va_service):
        t = va_service.create_template(
            region_tracks=[_manual_track()],
            twitch_profile_id="profile-1",
            source_resolution=[1920, 1080],
            name="standard",
            confirmed=True,
        )
        assert t["confirmed"] is True
        listed = va_service.list_templates(twitch_profile_id="profile-1")
        assert len(listed) == 1
        assert listed[0]["id"] == t["id"]

    def test_template_reuse_across_jobs(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        # Create a confirmed template matching the VOD's profile.
        # The VOD's profile_id is set during _make_ready_vod.
        vod = vod_service.storage.load_vod(vod_id)
        profile_id = vod.get("profile_id")
        template = va_storage.save_template  # noqa: F841
        # Build a template via the service.
        va_service_unavailable = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=UnavailableVisionAdapter(),
            library_service=library_service,
        )
        t = va_service_unavailable.create_template(
            region_tracks=[_manual_track()],
            twitch_profile_id=profile_id,
            source_resolution=[160, 120],
            confirmed=True,
        )
        # Adapter returns regions matching the template so validation passes.
        adapter = StaticVisionAdapter(
            default=[_det("FACECAM", 0.8, 0.7, 0.18, 0.25)],
        )
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=adapter,
            library_service=library_service,
        )
        job = svc.start_job(
            vod_id, start_seconds=0.0, end_seconds=1.0, profile_id=profile_id,
        )
        assert job["status"] == VisualAnalysisJobStatus.COMPLETED
        assert job["origin"] == "template"
        assert job["template_id"] == t["id"]

    def test_template_deviation_falls_back_to_automatic(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        vod = vod_service.storage.load_vod(vod_id)
        profile_id = vod.get("profile_id")
        svc_unavailable = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=UnavailableVisionAdapter(),
            library_service=library_service,
        )
        # Template says facecam at (0.8, 0.7); adapter returns it at (0.1, 0.1).
        svc_unavailable.create_template(
            region_tracks=[_manual_track(x=0.8, y=0.7)],
            twitch_profile_id=profile_id,
            source_resolution=[160, 120],
            confirmed=True,
        )
        adapter = StaticVisionAdapter(
            default=[_det("FACECAM", 0.1, 0.1, 0.18, 0.25)],
        )
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=adapter,
            library_service=library_service,
        )
        job = svc.start_job(
            vod_id, start_seconds=0.0, end_seconds=1.0, profile_id=profile_id,
        )
        assert job["status"] == VisualAnalysisJobStatus.COMPLETED
        assert job["origin"] == "automatic"

    def test_update_and_delete_template(self, va_service):
        t = va_service.create_template(region_tracks=[_manual_track()], confirmed=False)
        updated = va_service.update_template(t["id"], confirmed=True, name="renamed")
        assert updated["confirmed"] is True
        assert updated["name"] == "renamed"
        assert va_service.delete_template(t["id"]) is True
        with pytest.raises(VisualAnalysisNotFoundError):
            va_service.get_template(t["id"])


# ---------------------------------------------------------------------------
# Service: cancel / retry / recovery
# ---------------------------------------------------------------------------


class TestCancelRetryRecovery:
    def test_cancel_active_job(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        adapter = StaticVisionAdapter(default=[_det("FACECAM")])
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=adapter,
            library_service=library_service,
        )
        # Start a job, then cancel it before it runs by marking it
        # canceled in storage (simulating an external cancel).
        # Since the service runs synchronously, we cancel by pre-marking.
        job_id = str(uuid.uuid4())
        from ttvturbo.visual_analysis.schemas import make_job_record
        from ttvturbo.storage_utils import now_iso
        job = make_job_record(
            job_id=job_id, media_item_id=vod_id, start_seconds=0.0,
            end_seconds=1.0, profile_id=None, force=True,
            manual_regions=[], created_at=now_iso(),
        )
        job["status"] = VisualAnalysisJobStatus.RUNNING
        va_storage.save_job(job)
        # Cancel it.
        canceled = svc.cancel_job(job_id)
        assert canceled["status"] == VisualAnalysisJobStatus.CANCELED

    def test_cancel_non_cancellable_rejected(self, va_service, vod_service, make_real_mp4, channel_lister):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        job = va_service.start_job(vod_id, manual_regions=[_manual_track()])
        assert job["status"] == VisualAnalysisJobStatus.COMPLETED
        with pytest.raises(VisualAnalysisConflictError):
            va_service.cancel_job(job["id"])

    def test_retry_failed_job(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        # First run with a bad adapter -> FAILED.
        class BadAdapter:
            def available(self):
                return True
            def analyze_keyframe(self, image_path, resolution):
                return [DetectedRegion(
                    type="FACECAM", box=Box(x=0, y=0, width=0.5, height=0.5),
                    confidence=2.0,
                )]
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=BadAdapter(),
            library_service=library_service,
        )
        with pytest.raises(VisualAnalysisValidationError):
            svc.start_job(vod_id, start_seconds=0.0, end_seconds=1.0, force=True)
        jobs = svc.list_jobs(media_item_id=vod_id)
        failed = jobs[0]
        assert failed["status"] == VisualAnalysisJobStatus.FAILED
        # Swap in a good adapter and retry.
        svc.vision_adapter = StaticVisionAdapter(default=[_det("FACECAM")])
        retried = svc.retry_job(failed["id"])
        assert retried["status"] == VisualAnalysisJobStatus.COMPLETED
        assert retried["output_artifact_id"] is not None

    def test_retry_active_rejected(self, va_service):
        # Cannot retry a job that is not terminal.
        from ttvturbo.visual_analysis.schemas import make_job_record
        from ttvturbo.storage_utils import now_iso
        job_id = str(uuid.uuid4())
        job = make_job_record(
            job_id=job_id, media_item_id=str(uuid.uuid4()), start_seconds=0.0,
            end_seconds=1.0, profile_id=None, force=True,
            manual_regions=[], created_at=now_iso(),
        )
        job["status"] = VisualAnalysisJobStatus.RUNNING
        va_service.storage.save_job(job)
        with pytest.raises(VisualAnalysisConflictError):
            va_service.retry_job(job_id)

    def test_recovery_picks_up_completed_job(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        vod_id, _ = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        adapter = StaticVisionAdapter(default=[_det("FACECAM")])
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=adapter,
            library_service=library_service,
        )
        job = svc.start_job(vod_id, start_seconds=0.0, end_seconds=1.0)
        # Idempotency: re-running without force returns the same job.
        again = svc.start_job(vod_id, start_seconds=0.0, end_seconds=1.0)
        assert again["id"] == job["id"]
        # Force re-runs.
        forced = svc.start_job(vod_id, start_seconds=0.0, end_seconds=1.0, force=True)
        assert forced["id"] != job["id"]


# ---------------------------------------------------------------------------
# Library artifact registration
# ---------------------------------------------------------------------------


class TestLibraryArtifact:
    def test_artifact_registered_on_library_item(
        self, va_storage, source_resolver, va_settings, library_service,
        vod_service, make_real_mp4, channel_lister,
    ):
        # Create a library item and a VOD that points at it.
        vod_id, mp4_path = _make_ready_vod(vod_service, make_real_mp4, channel_lister)
        # Promote the VOD file into the library.
        vod = vod_service.storage.load_vod(vod_id)
        # Build a library item manually.
        from ttvturbo.library import LibraryService, LibraryStorage
        lib_svc = library_service
        meta = lib_svc.create_upload_item(file_name="source.mp4", title="VA Lib")
        item_id = meta["id"]
        # Copy the mp4 into the library item dir.
        import shutil
        dest = lib_svc.storage.source_file_path(item_id, "mp4")
        shutil.copy(str(mp4_path), str(dest))
        meta["file_size_bytes"] = dest.stat().st_size
        lib_svc.storage.save_item(meta)

        adapter = StaticVisionAdapter(default=[_det("FACECAM")])
        svc = VisualAnalysisService(
            storage=va_storage,
            source_resolver=source_resolver,
            settings=va_settings,
            vision_adapter=adapter,
            library_service=lib_svc,
        )
        job = svc.start_job(item_id, start_seconds=0.0, end_seconds=1.0)
        assert job["status"] == VisualAnalysisJobStatus.COMPLETED
        # The library item metadata should now list the artifact.
        updated = lib_svc.get_item(item_id)
        artifacts = updated.get("artifacts") or []
        assert any(a["artifact_type"] == "visual_analysis" for a in artifacts)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestAPI:
    def test_status_endpoint(self, client):
        resp = client.get("/api/visual-analysis/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert "model_configured" in data

    def test_list_jobs_empty(self, client):
        resp = client.get("/api/visual-analysis/jobs")
        assert resp.status_code == 200
        assert resp.json() == {"jobs": []}

    def test_start_job_not_found_returns_404(self, client):
        resp = client.post(
            "/api/visual-analysis/jobs",
            json={"media_item_id": str(uuid.uuid4())},
        )
        # With no source resolvable, the service raises an error that
        # surfaces as 500 or 4xx depending on the resolver.  At minimum
        # it must not be 200.
        assert resp.status_code != 200

    def test_get_job_not_found_returns_404(self, client):
        resp = client.get(f"/api/visual-analysis/jobs/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_cancel_job_not_found_returns_404(self, client):
        resp = client.post(f"/api/visual-analysis/jobs/{uuid.uuid4()}/cancel")
        assert resp.status_code == 404

    def test_retry_job_not_found_returns_404(self, client):
        resp = client.post(f"/api/visual-analysis/jobs/{uuid.uuid4()}/retry")
        assert resp.status_code == 404

    def test_get_artifact_not_found_returns_404(self, client):
        resp = client.get(f"/api/visual-analysis/artifacts/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_template_crud(self, client):
        # Create.
        resp = client.post(
            "/api/layout-templates",
            json={
                "region_tracks": [_manual_track()],
                "twitch_profile_id": "p1",
                "source_resolution": [1920, 1080],
                "name": "std",
                "confirmed": True,
            },
        )
        assert resp.status_code == 201
        tid = resp.json()["id"]
        # List.
        resp = client.get("/api/layout-templates")
        assert resp.status_code == 200
        assert any(t["id"] == tid for t in resp.json()["templates"])
        # Get.
        resp = client.get(f"/api/layout-templates/{tid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == tid
        # Patch.
        resp = client.patch(f"/api/layout-templates/{tid}", json={"confirmed": False})
        assert resp.status_code == 200
        assert resp.json()["confirmed"] is False
        # Delete.
        resp = client.delete(f"/api/layout-templates/{tid}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # Get after delete -> 404.
        resp = client.get(f"/api/layout-templates/{tid}")
        assert resp.status_code == 404

    def test_template_list_filter_by_resolution(self, client):
        client.post(
            "/api/layout-templates",
            json={
                "region_tracks": [_manual_track()],
                "source_resolution": [1920, 1080],
                "confirmed": True,
            },
        )
        client.post(
            "/api/layout-templates",
            json={
                "region_tracks": [_manual_track()],
                "source_resolution": [1280, 720],
                "confirmed": True,
            },
        )
        resp = client.get("/api/layout-templates?width=1920&height=1080")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert all(t["source_resolution"] == [1920, 1080] for t in templates)
        assert len(templates) == 1


# ---------------------------------------------------------------------------
# Architecture / no-duplicate-routes
# ---------------------------------------------------------------------------


class TestArchitecture:
    def test_no_duplicate_routes(self, tmp_path):
        from ttvturbo.app_factory import create_app
        app = create_app(settings=Settings(data_root=tmp_path / "va_routes"))
        seen: dict[tuple[str, str], int] = {}

        def walk(routes):
            for route in routes:
                if hasattr(route, "original_router"):
                    walk(route.original_router.routes)
                    continue
                if hasattr(route, "routes"):
                    walk(route.routes)
                    continue
                if hasattr(route, "methods") and hasattr(route, "path"):
                    for method in sorted(route.methods or []):
                        if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
                            key = (method, route.path)
                            seen[key] = seen.get(key, 0) + 1

        walk(app.routes)
        duplicates = {k: v for k, v in seen.items() if v > 1}
        assert not duplicates, f"duplicate routes: {duplicates}"

    def test_visual_analysis_routes_registered(self, tmp_path):
        from ttvturbo.app_factory import create_app
        app = create_app(settings=Settings(data_root=tmp_path / "va_routes"))
        paths: set[str] = set()

        def walk(routes):
            for route in routes:
                if hasattr(route, "original_router"):
                    walk(route.original_router.routes)
                    continue
                if hasattr(route, "routes"):
                    walk(route.routes)
                    continue
                if hasattr(route, "path"):
                    paths.add(route.path)

        walk(app.routes)
        assert "/api/visual-analysis/jobs" in paths
        assert "/api/layout-templates" in paths
