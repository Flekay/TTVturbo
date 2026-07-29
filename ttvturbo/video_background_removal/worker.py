"""Background removal worker using a persistent rembg session per job."""
from __future__ import annotations

import shutil
import sys
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageFilter

from ttvturbo.media_capabilities.frame_pipeline import (
    crop_image,
    encode_frames,
    extract_audio,
    extract_frames,
    fail,
    load_worker_job,
    mux_audio,
    normalized_box_at,
    prepare_dirs,
    save_result,
    source_descriptor,
    update_job,
)
from ttvturbo.media_capabilities.utils import sha256_file, video_metadata
from ttvturbo.media_processing.gpu_lock import GpuLock, GpuLockOwner


def _make_session(model: str, device: str):
    from rembg import new_session
    if device.lower().startswith("cuda"):
        try:
            return new_session(model, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        except TypeError:
            pass
    return new_session(model)




def _warp_previous_alpha(previous_source: Image.Image, current_source: Image.Image, previous_alpha: np.ndarray) -> np.ndarray:
    """Motion-compensate the previous alpha mask into the current frame.

    Optical flow substantially reduces trails when the subject moves. If
    OpenCV is unavailable, a deterministic resized mask is still used.
    """
    try:
        import cv2
        prev = np.asarray(previous_source.convert("RGB"))
        cur = np.asarray(current_source.convert("RGB"))
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_RGB2GRAY)
        cur_gray = cv2.cvtColor(cur, cv2.COLOR_RGB2GRAY)
        flow = cv2.calcOpticalFlowFarneback(prev_gray, cur_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        height, width = cur_gray.shape
        grid_x, grid_y = np.meshgrid(np.arange(width), np.arange(height))
        map_x = (grid_x - flow[..., 0]).astype(np.float32)
        map_y = (grid_y - flow[..., 1]).astype(np.float32)
        prior = previous_alpha.astype(np.float32)
        if prior.shape != (height, width):
            prior = cv2.resize(prior, (width, height), interpolation=cv2.INTER_LINEAR)
        return cv2.remap(prior, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        return np.asarray(
            Image.fromarray(np.clip(previous_alpha, 0, 255).astype(np.uint8), mode="L")
            .resize(current_source.size, Image.Resampling.BILINEAR),
            dtype=np.float32,
        )

def _background_for_frame(mode: str, source: Image.Image, size: tuple[int, int], desc: dict, frame_index: int, background_frames: list[Path]) -> Image.Image:
    if mode == "SOLID_COLOR":
        color = ImageColor.getcolor(desc["background"].get("color", "#000000"), "RGBA")
        return Image.new("RGBA", size, color)
    if mode == "BLURRED_ORIGINAL":
        return source.convert("RGBA").resize(size, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(float(desc["background"].get("blur_radius", 18.0))))
    if mode == "IMAGE_ASSET":
        with Image.open(desc["background_path"]) as image:
            return image.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    if mode == "VIDEO_ASSET" and background_frames:
        path = background_frames[min(frame_index, len(background_frames) - 1)]
        with Image.open(path) as image:
            return image.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    return Image.new("RGBA", size, (0, 0, 0, 0))


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m ttvturbo.video_background_removal.worker <job-dir>", file=sys.stderr)
        return 2
    job_dir = Path(args[0])
    try:
        desc = load_worker_job(job_dir)
        update_job(job_dir, status="RUNNING", progress=1, stage="probe")
        source = Path(desc["source_path"])
        expected_hash = str(desc.get("source_sha256") or "")
        if expected_hash and sha256_file(source) != expected_hash:
            return fail(job_dir, "source changed after the job was created", code="SOURCE_CHANGED", retryable=False)
        ffmpeg, ffprobe = desc["ffmpeg_path"], desc["ffprobe_path"]
        start = float(desc.get("start_seconds") or 0)
        end = desc.get("end_seconds")
        end = float(end) if end is not None else None
        meta = source_descriptor(ffprobe, source, start, end)
        fps = float(meta["effective_fps"])
        dirs = prepare_dirs(job_dir)
        frames = extract_frames(ffmpeg, source, dirs["frames"], start_seconds=start, end_seconds=meta["end_seconds"], fps=fps)
        audio = None
        if desc.get("preserve_audio", True) and meta.get("has_audio"):
            audio = extract_audio(ffmpeg, source, dirs["work"] / "audio.m4a", start_seconds=start, end_seconds=meta["end_seconds"])

        background_frames: list[Path] = []
        if desc["background"].get("mode") == "VIDEO_ASSET":
            bg_source = Path(desc["background_path"])
            background_frames = extract_frames(ffmpeg, bg_source, dirs["backgrounds"], start_seconds=0, end_seconds=meta["selected_duration_seconds"], fps=fps)

        update_job(job_dir, progress=8, stage="load_model")
        model_id = desc["model_id"]
        gpu = GpuLock(Path(desc["data_root"]), stale_seconds=float(desc.get("gpu_lock_stale_seconds") or 3600))
        owner = GpuLockOwner(gpu, owner_type="video_background_removal", job_id=desc["job_id"], timeout_seconds=0) if desc.get("device", "cpu").lower().startswith("cuda") else None
        if owner:
            owner.__enter__()
        try:
            session = _make_session(model_id, desc.get("device", "cpu"))
            from rembg import remove
            previous: np.ndarray | None = None
            previous_source: Image.Image | None = None
            total = len(frames)
            static_region = desc.get("region")
            track = desc.get("region_track")
            smoothing = float(desc.get("temporal_smoothing", 0.7))
            first_size = None
            composite_dir = dirs["work"] / "composite"
            composite_dir.mkdir(parents=True, exist_ok=True)
            for idx, frame_path in enumerate(frames):
                with Image.open(frame_path) as original:
                    original = original.convert("RGB")
                    box = normalized_box_at(track, start + idx / fps, static_region)
                    source_crop = crop_image(original, box)
                    if first_size is None:
                        first_size = source_crop.size
                    elif source_crop.size != first_size:
                        # A tracked region may change size over time. Output
                        # frames must retain one stable canvas size for a
                        # valid video stream, so normalize all crops to the
                        # first frame's dimensions.
                        source_crop = source_crop.resize(first_size, Image.Resampling.LANCZOS)
                    rgba = remove(source_crop, session=session).convert("RGBA")
                    alpha = np.asarray(rgba.getchannel("A"), dtype=np.float32)
                    if previous is not None and previous_source is not None and smoothing > 0.0:
                        previous_for_frame = _warp_previous_alpha(previous_source, source_crop, previous)
                        # A higher value means more temporal stabilization:
                        # retain more of the motion-compensated prior mask while
                        # still adapting to the current model prediction.
                        alpha = smoothing * previous_for_frame + (1.0 - smoothing) * alpha
                    previous = alpha.copy()
                    previous_source = source_crop.copy()
                    alpha_u8 = np.clip(alpha, 0, 255).astype(np.uint8)
                    mask = Image.fromarray(alpha_u8, mode="L")
                    if desc.get("edge_refinement", True):
                        mask = mask.filter(ImageFilter.GaussianBlur(radius=0.8))
                    rgba.putalpha(mask)
                    name = frame_path.name
                    rgba.save(dirs["processed"] / name)
                    mask.save(dirs["alpha"] / name)
                    if "COMPOSITED_VIDEO" in desc["output_modes"]:
                        bg = _background_for_frame(desc["background"].get("mode"), source_crop, rgba.size, desc, idx, background_frames)
                        bg.alpha_composite(rgba).convert("RGB").save(composite_dir / name)
                if idx % 5 == 0 or idx + 1 == total:
                    update_job(job_dir, progress=10 + 65 * (idx + 1) / total, stage="remove_background")
        finally:
            if owner:
                owner.__exit__(None, None, None)

        outputs = []
        if "ALPHA_MASK" in desc["output_modes"]:
            silent = dirs["work"] / "mask_silent.mp4"
            encode_frames(ffmpeg, dirs["alpha"], silent, fps=fps, quality="FINAL")
            out = job_dir / "alpha_mask.mp4"
            mux_audio(ffmpeg, silent, None, out)
            outputs.append({"type": "VIDEO_ALPHA_MASK", "file_name": out.name, "container": "mp4", "file_size_bytes": out.stat().st_size})
        if "TRANSPARENT_VIDEO" in desc["output_modes"]:
            silent = dirs["work"] / "transparent_silent.mov"
            encode_frames(ffmpeg, dirs["processed"], silent, fps=fps, alpha=True)
            out = job_dir / "transparent.mov"
            mux_audio(ffmpeg, silent, audio, out, alpha=True)
            outputs.append({"type": "VIDEO_WITH_ALPHA", "file_name": out.name, "container": "mov", "file_size_bytes": out.stat().st_size})
        if "COMPOSITED_VIDEO" in desc["output_modes"]:
            silent = dirs["work"] / "composite_silent.mp4"
            encode_frames(ffmpeg, dirs["work"] / "composite", silent, fps=fps, quality="FINAL")
            out = job_dir / "composited.mp4"
            mux_audio(ffmpeg, silent, audio, out)
            outputs.append({"type": "VIDEO_BACKGROUND_REPLACED", "file_name": out.name, "container": "mp4", "file_size_bytes": out.stat().st_size})
        if not outputs:
            return fail(job_dir, "no outputs were requested")
        primary = job_dir / outputs[0]["file_name"]
        out_meta = video_metadata(ffprobe, primary)
        result = {
            "success": True,
            "model_id": model_id,
            "source_resolution": [meta["width"], meta["height"]],
            "output_resolution": [out_meta["width"], out_meta["height"]],
            "duration_seconds": out_meta["duration_seconds"],
            "fps": out_meta["fps"],
            "outputs": outputs,
            "effective_options": {
                "mode": desc["mode"],
                "output_modes": desc["output_modes"],
                "background": desc["background"],
                "temporal_smoothing": desc.get("temporal_smoothing"),
                "edge_refinement": desc.get("edge_refinement"),
            },
            "error": None,
        }
        save_result(job_dir, result)
        update_job(job_dir, status="COMPLETED", progress=100, stage=None)
        return 0
    except Exception as exc:
        traceback.print_exc()
        return fail(job_dir, str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
