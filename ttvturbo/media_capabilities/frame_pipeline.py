"""Frame-oriented helpers used by video AI worker subprocesses."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from PIL import Image

from ttvturbo.storage_utils import atomic_write_json, read_json
from .storage import CapabilityStorageError
from .utils import now_iso, video_metadata

_UNSET = object()


def load_worker_job(job_dir: Path) -> dict[str, Any]:
    return read_json(job_dir / "worker_job.json", CapabilityStorageError, kind="worker-job")


def load_job(job_dir: Path) -> dict[str, Any]:
    return read_json(job_dir / "job.json", CapabilityStorageError, kind="job")


def save_job(job_dir: Path, job: dict[str, Any]) -> None:
    atomic_write_json(job_dir / "job.json", job, CapabilityStorageError, kind="job")


def save_result(job_dir: Path, result: dict[str, Any]) -> None:
    atomic_write_json(job_dir / "result.json", result, CapabilityStorageError, kind="result")


def update_job(job_dir: Path, *, status: Optional[str] = None, progress: Optional[float] = None, stage: object = _UNSET, error: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    job = load_job(job_dir)
    if status is not None:
        job["status"] = status
    if progress is not None:
        job["progress"] = max(0.0, min(100.0, float(progress)))
    if stage is not _UNSET:
        job["current_stage"] = stage
    if error is not None:
        job["error"] = error
    if status == "RUNNING" and not job.get("started_at"):
        job["started_at"] = now_iso()
    if status in {"COMPLETED", "FAILED", "CANCELED"}:
        job["completed_at"] = now_iso()
    job["worker_pid"] = os.getpid()
    save_job(job_dir, job)
    return job


def fail(job_dir: Path, message: str, *, code: str = "PROCESSING_FAILED", retryable: bool = True) -> int:
    save_result(job_dir, {"success": False, "error": message})
    update_job(job_dir, status="FAILED", stage=None, error={"code": code, "message": message, "retryable": retryable})
    return 1


def run_cmd(cmd: list[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd[:8])}\n{stderr}")
    return proc


def prepare_dirs(job_dir: Path) -> dict[str, Path]:
    work = job_dir / "work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    frames = work / "frames"
    processed = work / "processed"
    alpha = work / "alpha"
    backgrounds = work / "backgrounds"
    for path in (frames, processed, alpha, backgrounds):
        path.mkdir(parents=True, exist_ok=True)
    return {"work": work, "frames": frames, "processed": processed, "alpha": alpha, "backgrounds": backgrounds}


def extract_frames(
    ffmpeg: str,
    source: Path,
    dest: Path,
    *,
    start_seconds: float,
    end_seconds: Optional[float],
    fps: float,
) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start_seconds > 0:
        cmd += ["-ss", f"{start_seconds:.6f}"]
    cmd += ["-i", str(source)]
    if end_seconds is not None:
        duration = max(0.0, end_seconds - start_seconds)
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-vf", f"fps={fps:.8f}", "-vsync", "0", str(dest / "%08d.png")]
    run_cmd(cmd)
    paths = sorted(dest.glob("*.png"))
    if not paths:
        raise RuntimeError("frame extraction produced no frames")
    return paths


def extract_audio(
    ffmpeg: str,
    source: Path,
    dest: Path,
    *,
    start_seconds: float,
    end_seconds: Optional[float],
) -> Optional[Path]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start_seconds > 0:
        cmd += ["-ss", f"{start_seconds:.6f}"]
    cmd += ["-i", str(source)]
    if end_seconds is not None:
        cmd += ["-t", f"{max(0.0, end_seconds - start_seconds):.6f}"]
    cmd += ["-vn", "-c:a", "aac", "-b:a", "192k", str(dest)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        try:
            dest.unlink()
        except OSError:
            pass
        return None
    return dest


def encode_frames(
    ffmpeg: str,
    frames_dir: Path,
    output: Path,
    *,
    fps: float,
    alpha: bool = False,
    quality: str = "FINAL",
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if alpha:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", f"{fps:.8f}",
            "-i", str(frames_dir / "%08d.png"),
            "-c:v", "prores_ks", "-profile:v", "4",
            "-pix_fmt", "yuva444p10le",
            str(output),
        ]
    else:
        crf = "28" if quality == "PREVIEW" else "18"
        preset = "veryfast" if quality == "PREVIEW" else "medium"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", f"{fps:.8f}",
            "-i", str(frames_dir / "%08d.png"),
            "-c:v", "libx264", "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(output),
        ]
    run_cmd(cmd)


def mux_audio(ffmpeg: str, video: Path, audio: Optional[Path], output: Path, *, alpha: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if audio is None or not audio.is_file():
        shutil.move(str(video), str(output))
        return
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(output)]
    run_cmd(cmd)
    try:
        video.unlink()
    except OSError:
        pass


def normalized_box_at(track: Optional[dict[str, Any]], absolute_time: float, static_region: Optional[dict[str, Any]] = None) -> Optional[dict[str, float]]:
    if static_region:
        return {k: float(static_region[k]) for k in ("x", "y", "width", "height")}
    if not track:
        return None
    keyframes = sorted(track.get("keyframes") or [], key=lambda k: float(k.get("time") or 0.0))
    if not keyframes:
        return None
    if absolute_time <= float(keyframes[0].get("time") or 0.0):
        return dict(keyframes[0]["box"])
    if absolute_time >= float(keyframes[-1].get("time") or 0.0):
        return dict(keyframes[-1]["box"])
    for left, right in zip(keyframes, keyframes[1:]):
        lt = float(left.get("time") or 0.0)
        rt = float(right.get("time") or 0.0)
        if lt <= absolute_time <= rt:
            ratio = 0.0 if rt <= lt else (absolute_time - lt) / (rt - lt)
            return {
                key: float(left["box"][key]) + (float(right["box"][key]) - float(left["box"][key])) * ratio
                for key in ("x", "y", "width", "height")
            }
    return dict(keyframes[-1]["box"])


def crop_image(image: Image.Image, box: Optional[dict[str, float]]) -> Image.Image:
    if box is None:
        return image.copy()
    width, height = image.size
    x0 = max(0, min(width - 1, int(round(float(box["x"]) * width))))
    y0 = max(0, min(height - 1, int(round(float(box["y"]) * height))))
    x1 = max(x0 + 1, min(width, int(round((float(box["x"]) + float(box["width"])) * width))))
    y1 = max(y0 + 1, min(height, int(round((float(box["y"]) + float(box["height"])) * height))))
    return image.crop((x0, y0, x1, y1))


def resize_dimensions(width: int, height: int, *, scale: Optional[int], target_width: Optional[int], target_height: Optional[int]) -> tuple[int, int]:
    if target_width and target_height:
        return int(target_width), int(target_height)
    factor = int(scale or 2)
    return max(1, width * factor), max(1, height * factor)


def source_descriptor(ffprobe: str, source: Path, start: float, end: Optional[float]) -> dict[str, Any]:
    meta = video_metadata(ffprobe, source)
    duration = meta["duration_seconds"]
    effective_end = min(duration, end) if end is not None and duration > 0 else (end if end is not None else duration)
    if effective_end is None or effective_end <= 0:
        effective_end = duration
    if effective_end <= start:
        raise RuntimeError("selected time range is empty")
    fps = meta["fps"] or 30.0
    return {**meta, "start_seconds": start, "end_seconds": effective_end, "selected_duration_seconds": effective_end - start, "effective_fps": fps}
