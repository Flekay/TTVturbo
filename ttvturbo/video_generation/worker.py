"""Video-generation worker subprocess.

Launched by :class:`VideoGenerationService` as a separate process.  This
module is the **concrete local adapter** required by the project spec:
it loads a real HuggingFace video-generation model via ``diffusers``
(CogVideoX family) and produces a real ``output.mp4``.  No simulated
results.

The worker:

* acquires the shared cross-process GPU lock (owner
  ``video_generation``);
* loads the configured diffusers pipeline lazily (only inside the run
  function so the module imports without GPU deps);
* for ``TEXT_TO_VIDEO`` uses ``CogVideoXPipeline``;
* for ``IMAGE_TO_VIDEO`` uses ``CogVideoXImageToVideoPipeline`` with the
  copied source image as the first frame;
* writes ``output.mp4`` + ``result.json`` and updates ``job.json``;
* unloads the model and clears the CUDA cache **before** releasing the
  GPU lock so the next owner sees freed VRAM;
* reports ``FAILED`` (never a fake success) when diffusers/torch are
  missing or the model id is empty.

Usage::

    python -m ttvturbo.video_generation.worker <worker_job.json>
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    import datetime as _dt

    return (
        _dt.datetime.now(tz=_dt.timezone.utc)
        .astimezone()
        .replace(microsecond=0)
        .isoformat()
    )


def _load_worker_job(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _save_job(job_dir: Path, job: dict) -> None:
    from ttvturbo.storage_utils import atomic_write_json

    atomic_write_json(job_dir / "job.json", job, Exception, kind="vg-job")


def _save_result(job_dir: Path, result: dict) -> None:
    from ttvturbo.storage_utils import atomic_write_json

    atomic_write_json(job_dir / "result.json", result, Exception, kind="vg-result")


def _check_dependencies() -> Optional[str]:
    """Return an error string if a dependency is missing, else None."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return "torch is not installed (see requirements-gpu.txt)"
    try:
        import diffusers  # noqa: F401
    except ImportError:
        return "diffusers is not installed (see requirements-gpu.txt)"
    return None


def _resolve_dtype(dtype: str):
    """Resolve a dtype string to a torch dtype."""
    import torch

    table = {
        "auto": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float16": torch.float16,
        "fp16": torch.float16,
        "float32": torch.float32,
        "fp32": torch.float32,
    }
    return table.get((dtype or "auto").lower(), torch.bfloat16)


def _load_pipeline(model_id: str, generation_type: str, dtype, cache_dir: Optional[str]):
    """Load the diffusers pipeline for the generation type."""
    from diffusers import CogVideoXImageToVideoPipeline, CogVideoXPipeline

    kwargs: dict[str, Any] = {"torch_dtype": dtype}
    if cache_dir:
        kwargs["cache_dir"] = cache_dir
    if generation_type == "IMAGE_TO_VIDEO":
        return CogVideoXImageToVideoPipeline.from_pretrained(model_id, **kwargs)
    return CogVideoXPipeline.from_pretrained(model_id, **kwargs)


def _to_device(pipe, device: str):
    """Move the pipeline to the target device (best-effort)."""
    try:
        return pipe.to(device)
    except Exception:
        return pipe


def _get_model_revision(pipe) -> Optional[str]:
    """Best-effort extraction of the model revision from the pipeline."""
    for attr in ("model_revision", "_model_revision", "revision"):
        rev = getattr(pipe, attr, None)
        if rev:
            return str(rev)
    config = getattr(pipe, "config", None)
    if isinstance(config, dict):
        rev = config.get("_model_revision") or config.get("revision")
        if rev:
            return str(rev)
    return None


def _load_source_image(path: Path):
    """Load the source image for I2V."""
    from PIL import Image

    return Image.open(str(path)).convert("RGB")


def _export_video(frames, output_path: Path, fps: int) -> None:
    """Export model frames to an mp4 file."""
    from diffusers.utils import export_to_video

    export_to_video(frames, str(output_path), fps=int(fps))


def _compute_duration_seconds(num_frames: int, fps: int) -> float:
    """Effective duration of the generated clip."""
    if fps <= 0:
        return 0.0
    return round((num_frames - 1) / fps, 3)


# ---------------------------------------------------------------------------
# Main worker entry point
# ---------------------------------------------------------------------------


def run_worker(worker_job_path: str) -> int:
    """Main worker entry point. Returns exit code (0 = success)."""
    wjob = _load_worker_job(worker_job_path)
    job_dir = Path(wjob["job_dir"])
    generation_type = wjob.get("type") or "TEXT_TO_VIDEO"
    model_id = wjob.get("model_id") or ""
    device = wjob.get("device") or "cuda"
    dtype_str = wjob.get("dtype") or "auto"
    cache_dir = wjob.get("model_cache_dir")
    gpu_lock_data_dir = wjob.get("gpu_lock_data_dir")
    gpu_lock_stale = float(wjob.get("gpu_lock_stale_seconds") or 3600.0)

    prompt = wjob.get("prompt") or ""
    seed = wjob.get("seed")
    aspect_ratio = wjob.get("aspect_ratio") or "16:9"
    resolution = wjob.get("resolution") or [720, 480]
    fps = int(wjob.get("fps") or 8)
    duration_seconds = float(wjob.get("duration_seconds") or 5.0)
    effective_options = wjob.get("effective_options") or {}
    source_image_path = wjob.get("source_image_path")

    num_frames = int(effective_options.get("num_frames", 49))
    num_inference_steps = int(effective_options.get("num_inference_steps", 50))
    guidance_scale = float(effective_options.get("guidance_scale", 6.0))
    negative_prompt = effective_options.get("negative_prompt") or None

    # Load the current job state.
    with open(job_dir / "job.json", "r", encoding="utf-8-sig") as fh:
        job = json.load(fh)
    job_id = job["id"]

    def _fail(message: str) -> int:
        job["status"] = "FAILED"
        job["error"] = {"code": "VG_WORKER", "message": message, "retryable": False}
        job["completed_at"] = _now_iso()
        _save_job(job_dir, job)
        _save_result(job_dir, {
            "success": False,
            "model_id": model_id,
            "model_revision": None,
            "prompt": prompt,
            "seed": int(seed) if seed is not None else 0,
            "duration_seconds": 0.0,
            "resolution": list(resolution),
            "fps": fps,
            "file_name": None,
            "file_size_bytes": 0,
            "effective_options": effective_options,
            "error": message,
        })
        print(f"FAIL: {message}", file=sys.stderr)
        return 1

    # Validate model id.
    if not model_id.strip():
        return _fail("video-generation model is not configured")

    # Validate dependencies.
    dep_error = _check_dependencies()
    if dep_error is not None:
        return _fail(dep_error)

    # Acquire GPU lock.
    from ttvturbo.media_processing.gpu_lock import (
        GpuLock,
        GpuLockOwner,
        OWNER_VIDEO_GENERATION,
    )

    lock = GpuLock(Path(gpu_lock_data_dir), stale_seconds=gpu_lock_stale)
    lock_ctx = None
    try:
        lock_ctx = GpuLockOwner(
            lock,
            owner_type=OWNER_VIDEO_GENERATION,
            job_id=f"vg-{job_id}",
        )
        lock_ctx.__enter__()
    except Exception as exc:
        return _fail(f"could not acquire GPU lock: {exc}")

    try:
        # Mark running.
        job["status"] = "RUNNING"
        job["started_at"] = _now_iso()
        job["current_stage"] = "load_model"
        job["progress"] = 5.0
        job["worker_pid"] = os.getpid()
        _save_job(job_dir, job)

        import time as _time

        load_t0 = _time.monotonic()
        try:
            dtype = _resolve_dtype(dtype_str)
            pipe = _load_pipeline(model_id, generation_type, dtype, cache_dir)
            pipe = _to_device(pipe, device)
        except Exception as exc:
            traceback.print_exc()
            return _fail(f"could not load model: {exc}")
        load_seconds = round(_time.monotonic() - load_t0, 3)
        model_revision = _get_model_revision(pipe)

        job["model"] = {"model_id": model_id, "model_revision": model_revision}
        job["current_stage"] = "generate"
        job["progress"] = 30.0
        _save_job(job_dir, job)

        # Build generator for reproducibility.
        import torch

        generator = torch.Generator(device=device if device.startswith("cuda") else "cpu")
        if seed is not None:
            generator = generator.manual_seed(int(seed))

        gen_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "num_frames": num_frames,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "height": int(resolution[1]),
            "width": int(resolution[0]),
            "generator": generator,
        }
        if negative_prompt:
            gen_kwargs["negative_prompt"] = negative_prompt
        if generation_type == "IMAGE_TO_VIDEO":
            if not source_image_path:
                return _fail("IMAGE_TO_VIDEO requires a source image")
            image = _load_source_image(Path(source_image_path))
            gen_kwargs["image"] = image

        try:
            output = pipe(**gen_kwargs)
        except Exception as exc:
            traceback.print_exc()
            return _fail(f"generation failed: {exc}")

        frames = output.frames[0] if hasattr(output, "frames") else output[0]

        job["current_stage"] = "export_video"
        job["progress"] = 80.0
        _save_job(job_dir, job)

        output_path = job_dir / "output.mp4"
        try:
            _export_video(frames, output_path, fps)
        except Exception as exc:
            traceback.print_exc()
            return _fail(f"video export failed: {exc}")

        if not output_path.is_file():
            return _fail("video export produced no file")
        file_size = output_path.stat().st_size
        if file_size <= 0:
            return _fail("generated video file is empty")

        effective_duration = _compute_duration_seconds(num_frames, fps)
        # If the model produced a different effective duration, record that.
        if effective_duration <= 0:
            effective_duration = duration_seconds

        # Write result.json.
        result = {
            "success": True,
            "model_id": model_id,
            "model_revision": model_revision,
            "prompt": prompt,
            "seed": int(seed) if seed is not None else 0,
            "duration_seconds": float(effective_duration),
            "resolution": [int(resolution[0]), int(resolution[1])],
            "fps": int(fps),
            "file_name": "output.mp4",
            "file_size_bytes": int(file_size),
            "effective_options": effective_options,
            "error": None,
        }
        _save_result(job_dir, result)

        # Mark job completed.  Final artifact registration is done by the
        # service orchestrator (which owns the library).
        job["status"] = "COMPLETED"
        job["progress"] = 100.0
        job["current_stage"] = None
        job["completed_at"] = _now_iso()
        _save_job(job_dir, job)

        # Unload the model and clear CUDA cache before releasing the GPU
        # lock so the next owner sees freed VRAM.
        del pipe
        del output
        del frames
        try:
            import gc

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        print(
            f"OK: generated {output_path.name} "
            f"({file_size} bytes, {effective_duration}s, {fps}fps)"
        )
        return 0

    finally:
        if lock_ctx is not None:
            try:
                lock_ctx.__exit__(None, None, None)
            except Exception:
                pass


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: video_generation_worker <worker_job.json>", file=sys.stderr)
        return 2
    return run_worker(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
