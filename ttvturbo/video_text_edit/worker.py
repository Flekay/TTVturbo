"""Diffusers-backed text inpaint / instruction edit worker for videos.

Frames are edited sequentially with a fixed seed. A lightweight optical-flow
warp of the previous edited frame is blended into the next input to reduce
frame-to-frame flicker. The original media remains untouched.
"""
from __future__ import annotations

import gc
import sys
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ttvturbo.media_capabilities.frame_pipeline import (
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


def _dtype(torch, name: str):
    value = (name or "float16").lower()
    if value == "bfloat16": return torch.bfloat16
    if value == "float32": return torch.float32
    return torch.float16


def _pipeline_revision(pipe) -> str | None:
    try:
        return getattr(getattr(pipe, "config", None), "_commit_hash", None)
    except Exception:
        return None


def _fit_model_size(image: Image.Image, max_side: int) -> tuple[Image.Image, tuple[int, int]]:
    original = image.size
    scale = min(1.0, float(max_side) / max(original))
    width = max(64, int(round(original[0] * scale / 8)) * 8)
    height = max(64, int(round(original[1] * scale / 8)) * 8)
    return image.resize((width, height), Image.Resampling.LANCZOS), original


def _region_mask(size: tuple[int, int], box: dict | None) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if box is None:
        draw.rectangle((0, 0, size[0], size[1]), fill=255)
    else:
        x0 = int(box["x"] * size[0]); y0 = int(box["y"] * size[1])
        x1 = int((box["x"] + box["width"]) * size[0]); y1 = int((box["y"] + box["height"]) * size[1])
        draw.rectangle((x0, y0, x1, y1), fill=255)
    return mask


def _warp_previous(previous_source: Image.Image, current_source: Image.Image, previous_output: Image.Image) -> Image.Image:
    try:
        import cv2
        prev = np.asarray(previous_source.convert("RGB"))
        cur = np.asarray(current_source.convert("RGB"))
        out = np.asarray(previous_output.convert("RGB"))
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_RGB2GRAY)
        cur_gray = cv2.cvtColor(cur, cv2.COLOR_RGB2GRAY)
        flow = cv2.calcOpticalFlowFarneback(prev_gray, cur_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        h, w = cur_gray.shape
        grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
        map_x = (grid_x - flow[..., 0]).astype(np.float32)
        map_y = (grid_y - flow[..., 1]).astype(np.float32)
        warped = cv2.remap(out, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        return Image.fromarray(warped)
    except Exception:
        return previous_output.resize(current_source.size, Image.Resampling.BILINEAR)


def _load_mask_frame(mask_path: Path | None, mask_frames: list[Path], idx: int, size: tuple[int, int]) -> Image.Image:
    if mask_frames:
        path = mask_frames[min(idx, len(mask_frames)-1)]
        with Image.open(path) as image:
            return image.convert("L").resize(size, Image.Resampling.BILINEAR)
    if mask_path:
        with Image.open(mask_path) as image:
            return image.convert("L").resize(size, Image.Resampling.BILINEAR)
    return Image.new("L", size, 255)


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m ttvturbo.video_text_edit.worker <job-dir>", file=sys.stderr)
        return 2
    job_dir = Path(args[0])
    pipe = None
    torch = None
    try:
        desc = load_worker_job(job_dir)
        update_job(job_dir, status="RUNNING", progress=1, stage="probe")
        source = Path(desc["source_path"])
        expected_hash = str(desc.get("source_sha256") or "")
        if expected_hash and sha256_file(source) != expected_hash:
            return fail(job_dir, "source changed after the job was created", code="SOURCE_CHANGED", retryable=False)
        ffmpeg, ffprobe = desc["ffmpeg_path"], desc["ffprobe_path"]
        start = float(desc.get("start_seconds") or 0)
        end = desc.get("end_seconds"); end = float(end) if end is not None else None
        meta = source_descriptor(ffprobe, source, start, end)
        fps = float(meta["effective_fps"])
        dirs = prepare_dirs(job_dir)
        frames = extract_frames(ffmpeg, source, dirs["frames"], start_seconds=start, end_seconds=meta["end_seconds"], fps=fps)
        audio = None
        if desc["options"].get("preserve_audio", True) and meta.get("has_audio"):
            audio = extract_audio(ffmpeg, source, dirs["work"] / "audio.m4a", start_seconds=start, end_seconds=meta["end_seconds"])

        mask_frames: list[Path] = []
        mask_path = Path(desc["mask_path"]) if desc.get("mask_path") else None
        if mask_path and mask_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            mask_frames = extract_frames(ffmpeg, mask_path, dirs["work"] / "mask_frames", start_seconds=0, end_seconds=meta["selected_duration_seconds"], fps=fps)
            mask_path = None

        import torch as _torch
        torch = _torch
        from diffusers import AutoPipelineForInpainting, StableDiffusionInstructPix2PixPipeline

        model_id = desc["model_id"]
        device = desc.get("device", "cuda")
        dtype = _dtype(torch, desc.get("dtype", "float16"))
        if not device.startswith("cuda") and dtype in {torch.float16, torch.bfloat16}:
            dtype = torch.float32
        update_job(job_dir, progress=8, stage="load_model")
        gpu = GpuLock(Path(desc["data_root"]), stale_seconds=float(desc.get("gpu_lock_stale_seconds") or 3600))
        owner = (
            GpuLockOwner(gpu, owner_type="video_text_edit", job_id=desc["job_id"], timeout_seconds=0)
            if device.startswith("cuda") else None
        )
        if owner:
            owner.__enter__()
        try:
            if desc["mode"] == "TEXT_INPAINT":
                pipe = AutoPipelineForInpainting.from_pretrained(model_id, torch_dtype=dtype, cache_dir=desc.get("cache_dir") or None)
            else:
                pipe = StableDiffusionInstructPix2PixPipeline.from_pretrained(model_id, torch_dtype=dtype, cache_dir=desc.get("cache_dir") or None)
            if device.startswith("cuda"):
                try: pipe.enable_model_cpu_offload()
                except Exception: pipe.to(device)
            else:
                pipe.to(device)

            seed = int(desc["seed"])
            options = desc["options"]
            temporal = float(options.get("temporal_consistency", 0.2))
            previous_source = None
            previous_output = None
            track = desc.get("region_track")
            static_region = desc.get("region")
            total = len(frames)
            for idx, frame_path in enumerate(frames):
                with Image.open(frame_path) as frame:
                    source_frame = frame.convert("RGB")
                model_image, original_size = _fit_model_size(source_frame, int(desc.get("max_processing_side") or 768))
                if previous_source is not None and previous_output is not None and temporal > 0:
                    warped = _warp_previous(previous_source.resize(model_image.size), model_image, previous_output.resize(model_image.size))
                    model_input = Image.blend(model_image, warped, temporal)
                else:
                    model_input = model_image
                generator = torch.Generator(device="cpu").manual_seed(seed)
                # A mask is required by inpainting and can also constrain an
                # instruction edit to a static/tracked/library mask. The
                # instruction pipeline itself has no mask input, so its result
                # is composited over the untouched source after inference.
                edit_mask = None
                if desc["mask_mode"] == "MASK_ASSET":
                    edit_mask = _load_mask_frame(mask_path, mask_frames, idx, model_image.size)
                elif desc["mask_mode"] != "FULL_FRAME":
                    box = normalized_box_at(track, start + idx / fps, static_region)
                    edit_mask = _region_mask(model_image.size, box)
                elif desc["mode"] == "TEXT_INPAINT":
                    edit_mask = Image.new("L", model_image.size, 255)

                kwargs = {
                    "prompt": desc["prompt"],
                    "image": model_input,
                    "num_inference_steps": int(options["num_inference_steps"]),
                    "guidance_scale": float(options["guidance_scale"]),
                    "generator": generator,
                }
                if desc.get("negative_prompt"):
                    kwargs["negative_prompt"] = desc["negative_prompt"]
                if desc["mode"] == "TEXT_INPAINT":
                    kwargs["mask_image"] = edit_mask
                    kwargs["strength"] = float(options["strength"])
                else:
                    kwargs["image_guidance_scale"] = float(options["image_guidance_scale"])
                output = pipe(**kwargs)
                nsfw = getattr(output, "nsfw_content_detected", None)
                if nsfw and any(bool(x) for x in nsfw):
                    return fail(job_dir, "model safety checker rejected an edited frame", code="SAFETY_REJECTED", retryable=False)
                edited_model = output.images[0].convert("RGB").resize(model_image.size, Image.Resampling.LANCZOS)
                if desc["mode"] == "TEXT_EDIT" and edit_mask is not None:
                    edited_model = Image.composite(edited_model, model_image, edit_mask.convert("L"))
                edited = edited_model.resize(original_size, Image.Resampling.LANCZOS)
                edited.save(dirs["processed"] / frame_path.name)
                previous_source = source_frame
                previous_output = edited
                if idx % 2 == 0 or idx + 1 == total:
                    update_job(job_dir, progress=12 + 73 * (idx + 1) / total, stage="edit_frames")

        finally:
            if owner:
                owner.__exit__(None, None, None)

        update_job(job_dir, progress=88, stage="encode")
        silent = dirs["work"] / "silent.mp4"
        encode_frames(ffmpeg, dirs["processed"], silent, fps=fps, quality=options.get("quality", "FINAL"))
        output_path = job_dir / "output.mp4"
        mux_audio(ffmpeg, silent, audio, output_path)
        out_meta = video_metadata(ffprobe, output_path)
        result = {
            "success": True,
            "mode": desc["mode"],
            "model_id": model_id,
            "model_revision": _pipeline_revision(pipe),
            "output_file": output_path.name,
            "source_resolution": [meta["width"], meta["height"]],
            "output_resolution": [out_meta["width"], out_meta["height"]],
            "duration_seconds": out_meta["duration_seconds"],
            "fps": out_meta["fps"],
            "file_size_bytes": output_path.stat().st_size,
            "seed": seed,
            "effective_options": options,
            "error": None,
        }
        save_result(job_dir, result)
        update_job(job_dir, status="COMPLETED", progress=100, stage=None)
        return 0
    except Exception as exc:
        traceback.print_exc()
        return fail(job_dir, str(exc))
    finally:
        try:
            del pipe
            gc.collect()
            if torch is not None and torch.cuda.is_available(): torch.cuda.empty_cache()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
