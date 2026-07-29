"""Real video upscale worker.

AUTO uses Real-ESRGAN NCNN when configured and available; otherwise it uses a
high-quality deterministic Lanczos frame scaler. Both paths produce a real
video and preserve source timing/audio.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from PIL import Image, ImageFilter

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
    resize_dimensions,
    save_result,
    source_descriptor,
    update_job,
)
from ttvturbo.media_capabilities.utils import sha256_file, video_metadata
from ttvturbo.media_processing.gpu_lock import GpuLock, GpuLockOwner


def _run_realesrgan(executable: str, input_dir: Path, output_dir: Path, *, scale: int, model_name: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [executable, "-i", str(input_dir), "-o", str(output_dir), "-s", str(scale), "-n", model_name, "-f", "png"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace")[-4000:] or "Real-ESRGAN failed")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m ttvturbo.video_upscale.worker <job-dir>", file=sys.stderr)
        return 2
    job_dir = Path(args[0])
    try:
        desc = load_worker_job(job_dir)
        update_job(job_dir, status="RUNNING", progress=1, stage="probe")
        source = Path(desc["source_path"])
        expected_hash = str(desc.get("source_sha256") or "")
        if expected_hash and sha256_file(source) != expected_hash:
            return fail(job_dir, "source changed after the job was created", code="SOURCE_CHANGED", retryable=False)
        ffmpeg = desc["ffmpeg_path"]
        ffprobe = desc["ffprobe_path"]
        start = float(desc.get("start_seconds") or 0.0)
        end = desc.get("end_seconds")
        end = float(end) if end is not None else None
        meta = source_descriptor(ffprobe, source, start, end)
        fps = float(meta["effective_fps"])
        dirs = prepare_dirs(job_dir)

        update_job(job_dir, progress=5, stage="extract_frames")
        frames = extract_frames(ffmpeg, source, dirs["frames"], start_seconds=start, end_seconds=meta["end_seconds"], fps=fps)
        audio = None
        if desc["options"].get("preserve_audio", True) and meta.get("has_audio"):
            audio = extract_audio(ffmpeg, source, dirs["work"] / "audio.m4a", start_seconds=start, end_seconds=meta["end_seconds"])

        static_region = desc.get("region")
        track = desc.get("region_track")
        options = desc["options"]
        requested_engine = str(options.get("engine") or "AUTO")
        realesrgan = desc.get("realesrgan_path")
        engine = "REALESRGAN" if requested_engine in {"AUTO", "REALESRGAN"} and realesrgan and Path(realesrgan).is_file() else "LANCZOS"
        if requested_engine == "REALESRGAN" and engine != "REALESRGAN":
            return fail(job_dir, "Real-ESRGAN was explicitly requested but the configured executable is unavailable", code="ENGINE_UNAVAILABLE")

        # Pre-crop into a stable frame sequence. This is required for moving
        # region tracks and keeps both engines behaviorally identical.
        cropped_dir = dirs["work"] / "cropped"
        cropped_dir.mkdir(parents=True, exist_ok=True)
        first_size = None
        total = len(frames)
        for index, path in enumerate(frames, start=1):
            with Image.open(path) as image:
                image = image.convert("RGB")
                box = normalized_box_at(track, start + (index - 1) / fps, static_region)
                cropped = crop_image(image, box)
                if options.get("deblock"):
                    cropped = cropped.filter(ImageFilter.MedianFilter(size=3))
                if options.get("denoise"):
                    cropped = cropped.filter(ImageFilter.SMOOTH_MORE)
                first_size = first_size or cropped.size
                cropped.save(cropped_dir / path.name, format="PNG")
            if index % 10 == 0 or index == total:
                update_job(job_dir, progress=5 + 20 * index / total, stage="prepare_frames")

        if first_size is None:
            return fail(job_dir, "no frames available")
        target_w, target_h = resize_dimensions(first_size[0], first_size[1], scale=options.get("scale"), target_width=options.get("target_width"), target_height=options.get("target_height"))

        if engine == "REALESRGAN":
            update_job(job_dir, progress=30, stage="realesrgan")
            gpu = GpuLock(Path(desc["data_root"]), stale_seconds=float(desc.get("gpu_lock_stale_seconds") or 3600))
            with GpuLockOwner(gpu, owner_type="video_upscale", job_id=desc["job_id"], timeout_seconds=0):
                model_scale = int(options.get("scale") or (4 if max(target_w / first_size[0], target_h / first_size[1]) > 2 else 2))
                _run_realesrgan(str(realesrgan), cropped_dir, dirs["processed"], scale=model_scale, model_name=desc["realesrgan_model"])
            # Force exact requested dimensions after neural upscale.
            for path in sorted(dirs["processed"].glob("*.png")):
                with Image.open(path) as image:
                    if image.size != (target_w, target_h):
                        image.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS).save(path)
        else:
            total = len(frames)
            for index, path in enumerate(sorted(cropped_dir.glob("*.png")), start=1):
                with Image.open(path) as image:
                    image.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS).save(dirs["processed"] / path.name)
                if index % 10 == 0 or index == total:
                    update_job(job_dir, progress=30 + 45 * index / total, stage="upscale_frames")

        update_job(job_dir, progress=80, stage="encode")
        silent = dirs["work"] / "silent.mp4"
        encode_frames(ffmpeg, dirs["processed"], silent, fps=fps, quality=options.get("quality", "FINAL"))
        output = job_dir / "output.mp4"
        mux_audio(ffmpeg, silent, audio, output)
        out_meta = video_metadata(ffprobe, output)
        if output.stat().st_size <= 0:
            return fail(job_dir, "upscale output is empty")
        result = {
            "success": True,
            "output_file": output.name,
            "engine": engine,
            "model_id": desc.get("realesrgan_model") if engine == "REALESRGAN" else "Pillow/Lanczos",
            "model_version": None,
            "source_resolution": [meta["width"], meta["height"]],
            "output_resolution": [out_meta["width"], out_meta["height"]],
            "duration_seconds": out_meta["duration_seconds"],
            "fps": out_meta["fps"],
            "file_size_bytes": output.stat().st_size,
            "effective_options": options,
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
