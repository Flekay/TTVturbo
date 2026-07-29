from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from ttvturbo.editing import EditDatabase, EditProjectService
from ttvturbo.library import LibraryService, LibraryStorage
from ttvturbo.media_capabilities.utils import sha256_file, video_metadata
from ttvturbo.media_processing import GpuLock
from ttvturbo.rendering import RenderingService, RenderingStorage
from ttvturbo.settings import Settings
from ttvturbo.video_background_removal import (
    VideoBackgroundRemovalService,
    VideoBackgroundRemovalStorage,
)
from ttvturbo.video_text_edit import VideoTextEditService, VideoTextEditStorage
from ttvturbo.video_upscale import VideoUpscaleService, VideoUpscaleStorage
from ttvturbo.video_cut import VideoCutService, VideoCutStorage


@pytest.fixture()
def tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe unavailable")
    return ffmpeg, ffprobe


def _make_video(path: Path, ffmpeg: str, *, width: int = 96, height: int = 64, fps: int = 6, duration: float = 1.0) -> None:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration}:size={width}x{height}:rate={fps}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert path.is_file() and path.stat().st_size > 0


def _library_video(library: LibraryService, ffmpeg: str, *, title: str = "Source") -> tuple[dict, Path]:
    item = library.create_upload_item("source.mp4", title=title, duration_seconds=1.0)
    path = library.storage.item_dir(item["id"]) / "source.mp4"
    _make_video(path, ffmpeg)
    item.update({
        "file_name": "source.mp4",
        "container": "mp4",
        "duration_seconds": 1.0,
        "file_size_bytes": path.stat().st_size,
    })
    library.storage.save_item(item)
    return item, path


def _sync_worker(main):
    def runner(_payload: dict, job_dir: Path) -> None:
        code = main([str(job_dir)])
        if code != 0:
            result = job_dir / "result.json"
            message = result.read_text(encoding="utf-8") if result.exists() else "no result"
            raise RuntimeError(f"worker failed ({code}): {message}")
    return runner


def _fake_background_worker(payload: dict, job_dir: Path) -> None:
    from ttvturbo.media_capabilities.frame_pipeline import save_result, update_job

    update_job(job_dir, status="RUNNING", progress=20, stage="remove_background")
    source = Path(payload["source_path"])
    outputs = []
    for mode in payload["output_modes"]:
        if mode == "ALPHA_MASK":
            name, kind, container = "alpha_mask.mp4", "VIDEO_ALPHA_MASK", "mp4"
        elif mode == "TRANSPARENT_VIDEO":
            name, kind, container = "transparent.mov", "VIDEO_WITH_ALPHA", "mov"
        else:
            name, kind, container = "composited.mp4", "VIDEO_BACKGROUND_REPLACED", "mp4"
        dest = job_dir / name
        shutil.copy2(source, dest)
        outputs.append({"type": kind, "file_name": name, "container": container, "file_size_bytes": dest.stat().st_size})
    save_result(job_dir, {
        "success": True,
        "model_id": payload["model_id"],
        "source_resolution": [96, 64],
        "output_resolution": [96, 64],
        "duration_seconds": 1.0,
        "fps": 6.0,
        "outputs": outputs,
        "effective_options": {"mode": payload["mode"]},
        "error": None,
    })
    update_job(job_dir, status="COMPLETED", progress=100, stage=None)


def _fake_text_worker(payload: dict, job_dir: Path) -> None:
    from ttvturbo.media_capabilities.frame_pipeline import save_result, update_job

    update_job(job_dir, status="RUNNING", progress=25, stage="edit_frames")
    output = job_dir / "output.mp4"
    shutil.copy2(payload["source_path"], output)
    save_result(job_dir, {
        "success": True,
        "mode": payload["mode"],
        "model_id": payload["model_id"],
        "model_revision": "test-revision",
        "output_file": output.name,
        "source_resolution": [96, 64],
        "output_resolution": [96, 64],
        "duration_seconds": 1.0,
        "fps": 6.0,
        "file_size_bytes": output.stat().st_size,
        "seed": payload["seed"],
        "effective_options": payload["options"],
        "error": None,
    })
    update_job(job_dir, status="COMPLETED", progress=100, stage=None)


def test_real_lanczos_video_upscale_preserves_original(tmp_path: Path, tools: tuple[str, str]):
    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    settings.video_upscale_backend = "LANCZOS"
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg)
    original_hash = sha256_file(source)

    from ttvturbo.video_upscale.worker import main as upscale_worker

    service = VideoUpscaleService(
        storage=VideoUpscaleStorage(paths.video_upscale),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        worker_python="python",
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_sync_worker(upscale_worker),
    )
    job = service.start_job(
        media_item_id=item["id"],
        options={"scale": 2, "engine": "LANCZOS", "quality": "PREVIEW", "preserve_audio": True},
    )
    assert job["status"] == "COMPLETED"
    artifact = service.get_artifact(job["output_artifact_id"])
    output = library.item_file_path(artifact["library_item_id"])
    meta = video_metadata(ffprobe, output)
    assert (meta["width"], meta["height"]) == (192, 128)
    assert meta["has_audio"] is True
    assert sha256_file(source) == original_hash
    assert artifact["engine"] == "LANCZOS"


def test_background_person_mode_uses_person_model_and_registers_variants(tmp_path: Path, tools: tuple[str, str]):
    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    settings.video_background_removal_model_id = "general-test-model"
    settings.video_background_removal_person_model_id = "person-test-model"
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg)
    original_hash = sha256_file(source)
    service = VideoBackgroundRemovalService(
        storage=VideoBackgroundRemovalStorage(paths.video_background_removal),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_fake_background_worker,
    )
    service.runtime_status = lambda: {"available": True, "reasons": []}
    job = service.start_job(
        media_item_id=item["id"],
        mode="PERSON",
        output_modes=["ALPHA_MASK", "TRANSPARENT_VIDEO"],
        background={"mode": "TRANSPARENT"},
    )
    assert job["status"] == "COMPLETED"
    descriptor = service.storage.load_worker_job(job["id"])
    assert descriptor["model_id"] == "person-test-model"
    assert len(job["output_artifact_ids"]) == 2
    kinds = {service.get_artifact(aid)["artifact_type"] for aid in job["output_artifact_ids"]}
    assert kinds == {"VIDEO_ALPHA_MASK", "VIDEO_WITH_ALPHA"}
    assert sha256_file(source) == original_hash



def test_background_worker_handles_moving_region_and_transparent_mov(
    tmp_path: Path, tools: tuple[str, str], monkeypatch: pytest.MonkeyPatch
):
    import sys
    import types
    from PIL import Image

    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    settings.video_background_removal_device = "cpu"
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg)
    original_hash = sha256_file(source)

    def fake_remove(image, *, session=None):
        rgba = image.convert("RGBA")
        alpha = Image.new("L", rgba.size, 220)
        rgba.putalpha(alpha)
        return rgba

    fake_rembg = types.ModuleType("rembg")
    fake_rembg.__spec__ = None
    fake_rembg.new_session = lambda *args, **kwargs: object()
    fake_rembg.remove = fake_remove
    monkeypatch.setitem(sys.modules, "rembg", fake_rembg)

    class Visual:
        def get_artifact(self, _artifact_id):
            return {
                "media_item_id": item["id"],
                "region_tracks": [{
                    "id": "moving",
                    "keyframes": [
                        {"time": 0.0, "box": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.5}},
                        {"time": 1.0, "box": {"x": 0.2, "y": 0.1, "width": 0.3, "height": 0.4}},
                    ],
                }],
            }

    from ttvturbo.video_background_removal.worker import main as background_worker

    service = VideoBackgroundRemovalService(
        storage=VideoBackgroundRemovalStorage(paths.video_background_removal),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        visual_analysis_service=Visual(),
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_sync_worker(background_worker),
    )
    service.runtime_status = lambda: {"available": True, "reasons": []}
    job = service.start_job(
        media_item_id=item["id"],
        mode="REGION_TRACK",
        region_track_artifact_id=str(uuid.uuid4()),
        region_track_id="moving",
        output_modes=["ALPHA_MASK", "TRANSPARENT_VIDEO"],
        background={"mode": "TRANSPARENT"},
        temporal_smoothing=0.7,
    )
    assert job["status"] == "COMPLETED"
    artifacts = [service.get_artifact(x) for x in job["output_artifact_ids"]]
    transparent = next(a for a in artifacts if a["artifact_type"] == "VIDEO_WITH_ALPHA")
    assert transparent["container"] == "mov"
    transparent_path = library.item_file_path(transparent["library_item_id"] )
    assert transparent_path.suffix == ".mov"
    assert video_metadata(ffprobe, transparent_path)["has_audio"] is True
    assert sha256_file(source) == original_hash

def test_text_inpaint_is_seeded_non_destructive_and_persistent(tmp_path: Path, tools: tuple[str, str]):
    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    settings.video_text_inpaint_model_id = "inpaint-test-model"
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg)
    original_hash = sha256_file(source)
    service = VideoTextEditService(
        storage=VideoTextEditStorage(paths.video_text_edit),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_fake_text_worker,
    )
    service.runtime_status = lambda: {"available": True, "reasons": []}
    job = service.start_job(
        media_item_id=item["id"],
        mode="TEXT_INPAINT",
        prompt="replace the marked object with a blue cube",
        mask_mode="STATIC_REGION",
        region={"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4},
        options={"seed": 1234, "quality": "PREVIEW"},
    )
    assert job["status"] == "COMPLETED"
    artifact = service.get_artifact(job["output_artifact_id"])
    assert artifact["seed"] == 1234
    assert artifact["model_id"] == "inpaint-test-model"
    assert artifact["prompt"] == "replace the marked object with a blue cube"
    assert library.item_file_path(artifact["library_item_id"]).is_file()
    assert sha256_file(source) == original_hash



def test_text_inpaint_worker_processes_frames_with_temporal_pipeline(
    tmp_path: Path, tools: tuple[str, str], monkeypatch: pytest.MonkeyPatch
):
    import sys
    import types
    from PIL import Image, ImageEnhance

    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    settings.video_text_edit_device = "cpu"
    settings.video_text_edit_dtype = "float32"
    settings.video_text_edit_max_processing_side = 256
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg)
    original_hash = sha256_file(source)

    class FakeOutput:
        def __init__(self, image):
            self.images = [image]
            self.nsfw_content_detected = [False]

    class FakePipe:
        config = types.SimpleNamespace(_commit_hash="fake-diffusers-revision")

        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            return cls()

        def to(self, _device):
            return self

        def enable_model_cpu_offload(self):
            return None

        def __call__(self, **kwargs):
            image = kwargs["image"].convert("RGB")
            edited = ImageEnhance.Color(image).enhance(0.3)
            mask = kwargs.get("mask_image")
            if mask is not None:
                blue = Image.new("RGB", image.size, (20, 80, 220))
                edited = Image.composite(blue, edited, mask.convert("L"))
            return FakeOutput(edited)

    fake_diffusers = types.ModuleType("diffusers")
    fake_diffusers.AutoPipelineForInpainting = FakePipe
    fake_diffusers.StableDiffusionInstructPix2PixPipeline = FakePipe
    monkeypatch.setitem(sys.modules, "diffusers", fake_diffusers)

    from ttvturbo.video_text_edit.worker import main as text_worker

    service = VideoTextEditService(
        storage=VideoTextEditStorage(paths.video_text_edit),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_sync_worker(text_worker),
    )
    service.runtime_status = lambda: {"available": True, "reasons": []}
    job = service.start_job(
        media_item_id=item["id"],
        mode="TEXT_INPAINT",
        prompt="replace the center with blue",
        mask_mode="STATIC_REGION",
        region={"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
        options={
            "seed": 99,
            "num_inference_steps": 2,
            "quality": "PREVIEW",
            "temporal_consistency": 0.2,
        },
    )
    assert job["status"] == "COMPLETED"
    artifact = service.get_artifact(job["output_artifact_id"] )
    output = library.item_file_path(artifact["library_item_id"] )
    assert output.is_file()
    assert video_metadata(ffprobe, output)["has_audio"] is True
    assert artifact["model_revision"] == "fake-diffusers-revision"
    assert artifact["effective_options"]["max_processing_side"] == 256
    assert sha256_file(source) == original_hash

def test_real_preview_and_final_render_are_commit_pinned(tmp_path: Path, tools: tuple[str, str]):
    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg)
    original_hash = sha256_file(source)
    editing = EditProjectService(EditDatabase(paths.editing / "edit.sqlite3"), library_service=library)
    sequence_id = "desktop-test"
    project = editing.create_project(
        name="Render test",
        sources=[{"media_item_id": item["id"]}],
        sequences=[{
            "id": sequence_id,
            "name": "Desktop",
            "width": 320,
            "height": 240,
            "fps_numerator": 6,
            "fps_denominator": 1,
            "format_profile": "CUSTOM",
        }],
    )
    branch = project["branches"][0]
    commit = editing.create_commit(
        project["id"],
        branch_id=branch["id"],
        expected_head_commit_id=branch["head_commit_id"],
        message="Add source clip",
        operations=[
            {"type": "ADD_TRACK", "sequence_id": sequence_id, "payload": {"track": {"id": "video", "type": "VIDEO", "name": "Video"}}},
            {"type": "ADD_CLIP", "sequence_id": sequence_id, "payload": {"track_id": "video", "clip": {
                "id": "clip",
                "source_media_item_id": item["id"],
                "source_start_us": 0,
                "source_end_us": 1_000_000,
                "timeline_start_us": 0,
            }}},
        ],
    )
    from ttvturbo.rendering.worker import main as render_worker

    service = RenderingService(
        storage=RenderingStorage(paths.rendering),
        edit_project_service=editing,
        library_service=library,
        settings=settings,
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_sync_worker(render_worker),
    )
    preview = service.start_job(
        project_id=project["id"], sequence_id=sequence_id, commit_id=commit["id"],
        settings={"mode": "PREVIEW", "preview_max_dimension": 240},
    )
    assert preview["status"] == "COMPLETED"
    p_art = service.get_artifact(preview["output_artifact_id"])
    p_meta = video_metadata(ffprobe, library.item_file_path(p_art["library_item_id"]))
    assert max(p_meta["width"], p_meta["height"]) <= 240
    assert p_art["commit_id"] == commit["id"]
    assert p_art["state_hash"] == commit["state_hash"]

    final = service.start_job(
        project_id=project["id"], sequence_id=sequence_id, commit_id=commit["id"],
        settings={"mode": "FINAL"},
    )
    assert final["status"] == "COMPLETED"
    f_art = service.get_artifact(final["output_artifact_id"])
    f_meta = video_metadata(ffprobe, library.item_file_path(f_art["library_item_id"]))
    assert (f_meta["width"], f_meta["height"]) == (320, 240)
    assert f_meta["has_audio"] is True
    assert sha256_file(source) == original_hash

    cached = service.start_job(
        project_id=project["id"], sequence_id=sequence_id, commit_id=commit["id"],
        settings={"mode": "FINAL"},
    )
    assert cached["cached"] is True
    assert service.get_job(cached["id"])["output_artifact_id"] == final["output_artifact_id"]


def test_app_registers_all_new_backend_routes(tmp_path: Path):
    from ttvturbo.app_factory import create_app

    app = create_app(Settings(data_root=tmp_path / "data"))
    paths: set[str] = set()

    def _walk(route_list) -> None:
        for route in route_list:
            if hasattr(route, "original_router"):
                _walk(route.original_router.routes)
                continue
            if hasattr(route, "routes"):
                _walk(route.routes)
                continue
            if hasattr(route, "path"):
                paths.add(route.path)

    _walk(app.routes)
    assert "/api/video-upscale/jobs" in paths
    assert "/api/video-background-removal/jobs" in paths
    assert "/api/video-text-edit/jobs" in paths
    assert "/api/video-cut/jobs" in paths
    assert "/api/rendering/jobs" in paths


def test_new_capability_apis_map_missing_jobs_to_404(tmp_path: Path):
    from fastapi.testclient import TestClient
    from ttvturbo.app_factory import create_app

    app = create_app(Settings(data_root=tmp_path / "data"))
    missing = str(uuid.uuid4())
    with TestClient(app) as client:
        for prefix in (
            "/api/video-upscale",
            "/api/video-background-removal",
            "/api/video-text-edit",
            "/api/video-cut",
            "/api/rendering",
        ):
            response = client.get(f"{prefix}/jobs/{missing}")
            assert response.status_code == 404, (prefix, response.text)


def test_text_instruction_edit_respects_static_region(
    tmp_path: Path, tools: tuple[str, str], monkeypatch: pytest.MonkeyPatch
):
    """Instruction edits must leave pixels outside a requested region untouched."""
    import sys
    import types
    import numpy as np
    from PIL import Image

    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    settings.video_text_edit_device = "cpu"
    settings.video_text_edit_dtype = "float32"
    settings.video_text_edit_max_processing_side = 256
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg)

    class FakeOutput:
        def __init__(self, image):
            self.images = [image]
            self.nsfw_content_detected = [False]

    class FakePipe:
        config = types.SimpleNamespace(_commit_hash="region-test")

        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            return cls()

        def to(self, _device):
            return self

        def __call__(self, **kwargs):
            return FakeOutput(Image.new("RGB", kwargs["image"].size, (240, 20, 20)))

    fake_diffusers = types.ModuleType("diffusers")
    fake_diffusers.AutoPipelineForInpainting = FakePipe
    fake_diffusers.StableDiffusionInstructPix2PixPipeline = FakePipe
    monkeypatch.setitem(sys.modules, "diffusers", fake_diffusers)

    from ttvturbo.video_text_edit.worker import main as text_worker

    service = VideoTextEditService(
        storage=VideoTextEditStorage(paths.video_text_edit),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_sync_worker(text_worker),
    )
    service.runtime_status = lambda: {"available": True, "reasons": []}
    job = service.start_job(
        media_item_id=item["id"],
        mode="TEXT_EDIT",
        prompt="turn the selected area red",
        mask_mode="STATIC_REGION",
        region={"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
        options={"seed": 17, "num_inference_steps": 1, "quality": "PREVIEW", "temporal_consistency": 0},
    )
    assert job["status"] == "COMPLETED"
    artifact = service.get_artifact(job["output_artifact_id"])
    output = library.item_file_path(artifact["library_item_id"])

    source_png = tmp_path / "source.png"
    output_png = tmp_path / "output.png"
    for media, image in ((source, source_png), (output, output_png)):
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-frames:v", "1", str(image)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")

    src = np.asarray(Image.open(source_png).convert("RGB"), dtype=np.int16)
    out = np.asarray(Image.open(output_png).convert("RGB"), dtype=np.int16)
    outside_difference = np.abs(src[2:12, 2:12] - out[2:12, 2:12]).mean()
    inside_difference = np.abs(src[20:44, 30:66] - out[20:44, 30:66]).mean()
    assert inside_difference > outside_difference * 2


def test_renderer_does_not_pipe_unread_ffmpeg_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ttvturbo.rendering import worker

    captured = {}

    class FakeProcess:
        stdout = ["out_time_us=1000000\n", "progress=end\n"]

        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker, "update_job", lambda *args, **kwargs: None)

    code, stderr = worker._run_ffmpeg(["ffmpeg"], tmp_path, 1.0)

    assert code == 0
    assert stderr == ""
    assert captured["stdout"] is worker.subprocess.PIPE
    assert captured["stderr"] is not worker.subprocess.PIPE


def test_video_cut_crops_region_and_preserves_audio(tmp_path: Path, tools: tuple[str, str]):
    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, source = _library_video(library, ffmpeg, title="VOD source")
    original_hash = sha256_file(source)

    from ttvturbo.video_cut.worker import main as cut_worker

    service = VideoCutService(
        storage=VideoCutStorage(paths.video_cut),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        worker_python="python",
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        worker_runner=_sync_worker(cut_worker),
    )
    # Source is 96x64; select the top-left quarter (48x32).
    job = service.start_job(
        media_item_id=item["id"],
        region={"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.5},
        output_lifecycle="TEMPORARY",
        options={"preserve_audio": True, "quality": "FINAL"},
    )
    assert job["status"] == "COMPLETED"
    artifact = service.get_artifact(job["output_artifact_id"])
    assert artifact["artifact_type"] == "VIDEO_REGION_CUT"
    output = library.item_file_path(artifact["library_item_id"])
    meta = video_metadata(ffprobe, output)
    # Crop dimensions are rounded to even values; 48x32 are already even.
    assert (meta["width"], meta["height"]) == (48, 32)
    assert meta["has_audio"] is True
    assert sha256_file(source) == original_hash
    assert artifact["lifecycle"] == "TEMPORARY"


def test_video_cut_rejects_region_outside_frame(tmp_path: Path, tools: tuple[str, str]):
    ffmpeg, ffprobe = tools
    settings = Settings(data_root=tmp_path / "data")
    paths = settings.paths(); paths.ensure_dirs()
    library = LibraryService(LibraryStorage(paths.library))
    item, _ = _library_video(library, ffmpeg)

    service = VideoCutService(
        storage=VideoCutStorage(paths.video_cut),
        library_service=library,
        settings=settings,
        gpu_lock=GpuLock(paths.data_root),
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
    )
    service.runtime_status = lambda: {"available": True, "reasons": []}
    import pytest as _pytest
    with _pytest.raises(Exception):
        service.start_job(
            media_item_id=item["id"],
            region={"x": 0.8, "y": 0.8, "width": 0.5, "height": 0.5},
        )

