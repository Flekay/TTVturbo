"""Video region cut worker (ausschneiden).

Crops a rectangular region out of the source video and re-encodes it into a
new MP4.  Audio is taken from the source unchanged (re-encoded to AAC for
container compatibility).  A single ffmpeg pass with the ``crop`` filter is
used because no per-frame AI processing is required.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from ttvturbo.media_capabilities.frame_pipeline import (
    fail,
    load_worker_job,
    save_result,
    update_job,
)
from ttvturbo.media_capabilities.utils import sha256_file, video_metadata


def _resolve_executable(name: str, hint: str | None) -> str:
    """Resolve an executable path, falling back to PATH + Windows locations."""
    if hint and Path(hint).is_file():
        return hint
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        candidates: list[Path] = []
        if local_app:
            candidates.append(Path(local_app) / "Microsoft" / "WinGet" / "Packages")
        candidates += [Path("C:/Program Files"), Path("C:/ffmpeg"), Path("C:/tools/ffmpeg")]
        for base in candidates:
            if not base.exists():
                continue
            for hit in base.rglob(f"{name}.exe"):
                return str(hit)
    raise FileNotFoundError(f"Could not find {name} (hint={hint!r})")


def _run_ffmpeg_crop(
    ffmpeg: str,
    source: Path,
    output: Path,
    *,
    start_seconds: float,
    end_seconds: float | None,
    crop_w: int,
    crop_h: int,
    crop_x: int,
    crop_y: int,
    preserve_audio: bool,
    quality: str,
    is_image: bool = False,
) -> None:
    crf = "28" if quality == "PREVIEW" else "18"
    preset = "veryfast" if quality == "PREVIEW" else "medium"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    # Still images need -loop 1 so ffmpeg produces a video of the requested
    # duration instead of encoding a single frame.
    if is_image:
        cmd += ["-loop", "1"]
    if start_seconds > 0 and not is_image:
        cmd += ["-ss", f"{start_seconds:.6f}"]
    cmd += ["-i", str(source)]
    if end_seconds is not None:
        duration = max(0.0, end_seconds - start_seconds)
        cmd += ["-t", f"{duration:.6f}"]
    cmd += [
        "-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]
    if preserve_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += [str(output)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"ffmpeg crop failed ({proc.returncode}): {stderr}")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m ttvturbo.video_cut.worker <job-dir>", file=sys.stderr)
        return 2
    job_dir = Path(args[0])
    try:
        desc = load_worker_job(job_dir)
        update_job(job_dir, status="RUNNING", progress=5, stage="probe")
        source = Path(desc["source_path"])
        expected_hash = str(desc.get("source_sha256") or "")
        if expected_hash and sha256_file(source) != expected_hash:
            return fail(job_dir, "source changed after the job was created", code="SOURCE_CHANGED", retryable=False)
        ffmpeg = _resolve_executable("ffmpeg", desc.get("ffmpeg_path"))
        ffprobe = _resolve_executable("ffprobe", desc.get("ffprobe_path"))
        start = float(desc.get("start_seconds") or 0.0)
        end = desc.get("end_seconds")
        end = float(end) if end is not None else None

        meta = video_metadata(ffprobe, source)
        src_w = int(meta["width"])
        src_h = int(meta["height"])
        duration = meta["duration_seconds"]
        # Still images report duration 0 — treat them as single-frame sources.
        # Ignore the requested time range and produce a short video so the crop
        # result can be used like any other clip in the editor.
        is_image = duration <= 0
        if is_image:
            start = 0.0
            effective_end = 1.0
        else:
            effective_end = min(duration, end) if end is not None else duration
            if effective_end <= start:
                return fail(job_dir, "requested time range is empty", code="EMPTY_RANGE", retryable=False)

        region = desc["region"]
        rx = float(region["x"])
        ry = float(region["y"])
        rw = float(region["width"])
        rh = float(region["height"])
        # Convert normalized region to pixel coordinates (even values required
        # by yuv420p — round to nearest even).
        crop_x = int(round(rx * src_w))
        crop_y = int(round(ry * src_h))
        crop_w = int(round(rw * src_w))
        crop_h = int(round(rh * src_h))
        crop_x = max(0, min(crop_x, src_w - 2))
        crop_y = max(0, min(crop_y, src_h - 2))
        crop_w = max(2, min(crop_w, src_w - crop_x))
        crop_h = max(2, min(crop_h, src_h - crop_y))
        # libx264 with yuv420p requires even dimensions.
        crop_w -= crop_w % 2
        crop_h -= crop_h % 2
        if crop_w < 2 or crop_h < 2:
            return fail(job_dir, "selected region is too small to encode", code="REGION_TOO_SMALL", retryable=False)

        options = desc["options"]
        preserve_audio = bool(options.get("preserve_audio", True)) and bool(meta.get("has_audio"))
        quality = str(options.get("quality") or "FINAL")

        update_job(job_dir, progress=20, stage="cut")
        output = job_dir / "output.mp4"
        _run_ffmpeg_crop(
            ffmpeg, source, output,
            start_seconds=start,
            end_seconds=effective_end,
            crop_w=crop_w, crop_h=crop_h, crop_x=crop_x, crop_y=crop_y,
            preserve_audio=preserve_audio, quality=quality,
            is_image=is_image,
        )
        if not output.is_file() or output.stat().st_size <= 0:
            return fail(job_dir, "cut output is empty")

        update_job(job_dir, progress=90, stage="probe_output")
        out_meta = video_metadata(ffprobe, output)
        result = {
            "success": True,
            "output_file": output.name,
            "source_resolution": [src_w, src_h],
            "output_resolution": [out_meta["width"], out_meta["height"]],
            "duration_seconds": out_meta["duration_seconds"],
            "fps": out_meta["fps"],
            "file_size_bytes": output.stat().st_size,
            "effective_options": {
                "preserve_audio": preserve_audio,
                "quality": quality,
                "region": {"x": rx, "y": ry, "width": rw, "height": rh},
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
