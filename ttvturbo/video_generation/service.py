"""Video-generation service.

A reusable backend capability that generates real videos from a text
prompt or a text prompt + a library image asset.  No UI, no rendering.

Pipeline
--------
1. Validate the request (type, prompt length, duration, aspect ratio,
   seed, source image for I2V).
2. Resolve the input image (I2V only) via the central
   :class:`MediaSourceResolver` / library service and copy it into the
   job directory so the worker has a stable path.  No free file paths
   are ever accepted from the frontend -- only library asset ids.
3. Build the effective, whitelisted options.
4. Persist a QUEUED job record.
5. Spawn the worker subprocess (the concrete diffusers CogVideoX
   adapter, see :mod:`.worker`).  The worker acquires the shared GPU
   lock, loads the model lazily, generates the video, writes
   ``output.mp4`` + ``result.json`` and unloads the model.
6. The orchestrator thread polls job state.  On COMPLETED it finalizes
   the job: moves the output into the persistent library, creates the
   artifact record and links it on the job + the library item.  A
   FAILED generation is **never** registered as a final artifact.

Model integration
-----------------
* the model id(s) come from central :class:`Settings` -- never from the
  frontend;
* the model runs in a separate worker subprocess;
* the shared cross-process GPU lock is reused (owner
  ``video_generation``);
* the model is loaded lazily and unloaded after every job;
* the base application starts without any generation dependencies
  installed (the service reports ``available=false``).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from ttvturbo.settings import Settings
from ttvturbo.storage_utils import now_iso, validate_uuid

from .adapter import UnavailableVideoGenerationAdapter, VideoGenerationAdapter
from .schemas import (
    ASPECT_RATIOS,
    RESOLUTIONS_BY_ASPECT_RATIO,
    SCHEMA_VERSION,
    SUPPORTED_GENERATION_TYPES,
    VideoGenerationArtifact,
    VideoGenerationConflictError,
    VideoGenerationError,
    VideoGenerationNotFoundError,
    VideoGenerationStorageError,
    VideoGenerationUnavailableError,
    VideoGenerationValidationError,
    WHITELISTED_RESOLUTIONS,
    EffectiveOptions,
    GenerationResult,
    VideoGenerationJobStatus,
    make_job_record,
    resolution_for_aspect_ratio,
)
from .storage import VideoGenerationStorage

logger = logging.getLogger("ttvturbo.video_generation.service")

ARTIFACT_TYPE = "video_generation"
OPERATION = "video_generation"

ORCHESTRATOR_POLL_SECONDS = 2.0
KILL_GRACE_SECONDS = 5.0

# Default generation fps (CogVideoX native rate).
DEFAULT_FPS = 8


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return now_iso()


# A worker runner is a callable that executes the generation for a job.
# It receives the worker_job dict and the job directory.  The default
# implementation spawns the diffusers worker subprocess; tests inject a
# fake that produces a real mp4 without loading any model.
WorkerRunner = Callable[[dict, Path], None]


class VideoGenerationService:
    """Orchestrates video-generation jobs.

    The service creates job records, spawns a worker subprocess per job,
    polls for progress, finalizes completed jobs (library artifact
    registration) and handles cancel / retry / recovery.
    """

    def __init__(
        self,
        storage: VideoGenerationStorage,
        source_resolver: Any,
        settings: Settings,
        gpu_lock: Any,
        *,
        library_service: Optional[Any] = None,
        worker_python: Optional[str] = None,
        ffmpeg_path: Optional[str] = None,
        ffprobe_path: Optional[str] = None,
        adapter: Optional[VideoGenerationAdapter] = None,
        worker_runner: Optional[WorkerRunner] = None,
    ) -> None:
        self.storage = storage
        self.source_resolver = source_resolver
        self.settings = settings
        self.gpu_lock = gpu_lock
        self.library_service = library_service
        self._worker_python = worker_python or sys.executable
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe") or "ffprobe"
        self.adapter: VideoGenerationAdapter = adapter or UnavailableVideoGenerationAdapter()
        self._worker_runner = worker_runner
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen] = {}
        self._orchestrator_thread: Optional[threading.Thread] = None
        self._orchestrator_stop = threading.Event()
        self._runtime_cache: Optional[dict] = None
        self._runtime_cache_time: float = 0.0
        self._recover_on_startup()

    # ------------------------------------------------------------------ status
    def runtime_status(self) -> dict:
        """Return the video-generation capability status.

        Distinguishes the individual preconditions that must hold before
        a generation can succeed:

        * ``model_configured`` -- at least one model id is set;
        * ``dependencies_available`` -- diffusers + torch are importable
          in this process (the worker re-checks in its own process);
        * ``cuda_available`` -- torch sees CUDA (only relevant when
          device starts with ``cuda``);
        * ``worker_available`` -- the worker module is importable;
        * ``busy`` -- the shared GPU lock is currently held.
        """
        now = time.time()
        if self._runtime_cache is not None and (now - self._runtime_cache_time) < 10.0:
            return dict(self._runtime_cache)
        t2v_model = (self.settings.video_generation_t2v_model_id or "").strip()
        i2v_model = (self.settings.video_generation_i2v_model_id or "").strip()
        model_configured = bool(t2v_model or i2v_model)
        deps_ok, dep_reason = self._check_dependencies()
        cuda_available = self._check_cuda_available()
        device = self.settings.video_generation_device or "cuda"
        cuda_relevant = device.lower().startswith("cuda")
        worker_available = self._check_worker_module()
        adapter_available = bool(self.adapter.available())
        reasons: list[str] = []
        if not model_configured:
            reasons.append("no video-generation model configured")
        if not deps_ok:
            reasons.append(dep_reason or "dependencies missing")
        if cuda_relevant and not cuda_available:
            reasons.append("CUDA not available")
        if not worker_available:
            reasons.append("worker module not importable")
        if not adapter_available:
            reasons.append("adapter unavailable")
        available = (
            model_configured
            and deps_ok
            and worker_available
            and adapter_available
            and (not cuda_relevant or cuda_available)
        )
        busy_owner = None
        if self.gpu_lock is not None:
            busy_owner = (self.gpu_lock.current_owner() or {}).get("owner_type")
        status = {
            "available": available,
            "model_configured": model_configured,
            "dependencies_available": deps_ok,
            "cuda_available": cuda_available,
            "worker_available": worker_available,
            "adapter_available": adapter_available,
            "t2v_model": t2v_model,
            "i2v_model": i2v_model,
            "device": device,
            "dtype": self.settings.video_generation_dtype,
            "fps": self.settings.video_generation_fps,
            "max_duration_seconds": self.settings.video_generation_max_duration_seconds,
            "max_prompt_length": self.settings.video_generation_max_prompt_length,
            "busy": bool(busy_owner),
            "busy_owner_type": busy_owner,
            "reasons": reasons,
            "error": reasons[0] if reasons else None,
        }
        self._runtime_cache = status
        self._runtime_cache_time = now
        return dict(status)

    def capabilities(self) -> dict:
        """Return the advertised capability descriptor.

        Only generation types that are actually supported (with a
        configured model) are advertised.  Optional types (LIPSYNC,
        AVATAR_VIDEO) are never advertised in this phase.
        """
        status = self.runtime_status()
        advertised_types: list[str] = []
        if (self.settings.video_generation_t2v_model_id or "").strip():
            advertised_types.append("TEXT_TO_VIDEO")
        if (self.settings.video_generation_i2v_model_id or "").strip():
            advertised_types.append("IMAGE_TO_VIDEO")
        return {
            "available": status["available"],
            "generation_types": advertised_types,
            "aspect_ratios": sorted(ASPECT_RATIOS),
            "resolutions": {
                ratio: [w, h]
                for ratio, (w, h) in RESOLUTIONS_BY_ASPECT_RATIO.items()
            },
            "fps": [self.settings.video_generation_fps],
            "default_fps": self.settings.video_generation_fps,
            "max_duration_seconds": self.settings.video_generation_max_duration_seconds,
            "max_prompt_length": self.settings.video_generation_max_prompt_length,
            "duration_step_seconds": 1.0,
            "models": {
                "TEXT_TO_VIDEO": (self.settings.video_generation_t2v_model_id or "").strip() or None,
                "IMAGE_TO_VIDEO": (self.settings.video_generation_i2v_model_id or "").strip() or None,
            },
            "device": self.settings.video_generation_device,
            "dtype": self.settings.video_generation_dtype,
            "reasons": status["reasons"],
        }

    # ------------------------------------------------------------------ jobs
    def start_job(
        self,
        generation_type: str,
        prompt: str,
        *,
        source_image_asset_id: Optional[str] = None,
        duration_seconds: float = 5.0,
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        options: Optional[dict] = None,
    ) -> dict:
        """Start a video-generation job.

        Returns the job record.  When a synchronous worker runner is
        configured (tests) the returned record is already COMPLETED and
        finalized; otherwise it is QUEUED and the orchestrator finalizes
        asynchronously.
        """
        # Validate + build effective request.
        req = self._validate_request(
            generation_type=generation_type,
            prompt=prompt,
            source_image_asset_id=source_image_asset_id,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            seed=seed,
            options=options,
        )

        # Availability gate.
        status = self.runtime_status()
        if not status["available"]:
            raise VideoGenerationUnavailableError(
                "video generation is not available: " + "; ".join(status["reasons"])
            )

        # Concurrency guard.
        with self._lock:
            active = [jid for jid, p in self._active.items() if p.poll() is None]
            if len(active) >= max(1, self.settings.video_generation_max_concurrent):
                raise VideoGenerationConflictError(
                    "a video-generation job is already running"
                )

        job_id = _new_uuid()
        now = _now_iso()
        model_id = (
            self.settings.video_generation_t2v_model_id
            if req["generation_type"] == "TEXT_TO_VIDEO"
            else self.settings.video_generation_i2v_model_id
        ).strip()
        job = make_job_record(
            job_id=job_id,
            generation_type=req["generation_type"],
            prompt=req["prompt"],
            source_image_asset_id=req["source_image_asset_id"],
            duration_seconds=req["duration_seconds"],
            aspect_ratio=req["aspect_ratio"],
            seed=req["seed"],
            options=req["options"],
            effective_options=req["effective_options"],
            resolution=req["resolution"],
            fps=req["fps"],
            model_id=model_id,
            created_at=now,
        )
        self.storage.save_job(job)

        # Resolve + copy the source image for I2V so the worker has a
        # stable, validated path inside the job directory.
        source_image_path: Optional[str] = None
        if req["generation_type"] == "IMAGE_TO_VIDEO":
            try:
                source_image_path = self._copy_source_image(job_id, req["source_image_asset_id"])
            except Exception as exc:
                self._fail_job(job, exc)
                raise

        # Write the worker job descriptor.
        worker_job = self._build_worker_job(job, source_image_path)
        self.storage.save_worker_job(job_id, worker_job)

        # Launch the worker (subprocess or synchronous runner).
        self._launch_worker(job_id, sync=bool(self._worker_runner))

        # When a synchronous runner is used, finalize immediately so
        # callers see a COMPLETED + finalized job.
        if self._worker_runner is not None:
            self._finalize_job(job_id)

        self._ensure_orchestrator()
        return self.storage.load_job(job_id)

    def get_job(self, job_id: str) -> dict:
        validate_uuid(job_id, "job", VideoGenerationValidationError)
        return self.storage.load_job(job_id)

    def list_jobs(
        self,
        *,
        status_filter: Optional[str] = None,
        generation_type: Optional[str] = None,
    ) -> list[dict]:
        jobs = list(self.storage.iter_jobs())
        if status_filter:
            jobs = [j for j in jobs if j.get("status") == status_filter]
        if generation_type:
            jobs = [j for j in jobs if j.get("type") == generation_type]
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return jobs

    def cancel_job(self, job_id: str) -> dict:
        validate_uuid(job_id, "job", VideoGenerationValidationError)
        job = self.storage.load_job(job_id)
        if job.get("status") not in (
            VideoGenerationJobStatus.QUEUED,
            VideoGenerationJobStatus.RUNNING,
        ):
            raise VideoGenerationConflictError(
                f"job can only be canceled while active (current: {job.get('status')})"
            )
        job["status"] = VideoGenerationJobStatus.CANCELED
        job["completed_at"] = _now_iso()
        job["current_stage"] = None
        self.storage.save_job(job)
        # Terminate the worker if it is still running.
        with self._lock:
            proc = self._active.get(job_id)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("failed to terminate worker for %s: %s", job_id, exc)
        return self.storage.load_job(job_id)

    def retry_job(self, job_id: str) -> dict:
        validate_uuid(job_id, "job", VideoGenerationValidationError)
        job = self.storage.load_job(job_id)
        if job.get("status") in (
            VideoGenerationJobStatus.QUEUED,
            VideoGenerationJobStatus.RUNNING,
        ):
            raise VideoGenerationConflictError(
                "retry is only allowed for terminal jobs"
            )
        if job.get("status") not in (
            VideoGenerationJobStatus.FAILED,
            VideoGenerationJobStatus.CANCELED,
        ):
            raise VideoGenerationConflictError(
                "retry is only allowed for FAILED or CANCELED jobs"
            )
        # Availability gate.
        status = self.runtime_status()
        if not status["available"]:
            raise VideoGenerationUnavailableError(
                "video generation is not available: " + "; ".join(status["reasons"])
            )
        # Concurrency guard.
        with self._lock:
            active = [jid for jid, p in self._active.items() if p.poll() is None]
            if len(active) >= max(1, self.settings.video_generation_max_concurrent):
                raise VideoGenerationConflictError(
                    "a video-generation job is already running"
                )

        # Reset the job to QUEUED.
        job["status"] = VideoGenerationJobStatus.QUEUED
        job["error"] = None
        job["completed_at"] = None
        job["started_at"] = None
        job["progress"] = 0.0
        job["current_stage"] = None
        job["output_artifact_id"] = None
        job["library_item_id"] = None
        job["worker_pid"] = None
        self.storage.save_job(job)

        # Re-resolve the source image for I2V (the copy may have been
        # removed by a previous run).
        source_image_path: Optional[str] = None
        if job.get("type") == "IMAGE_TO_VIDEO" and job.get("source_image_asset_id"):
            source_image_path = self._copy_source_image(job_id, job["source_image_asset_id"])

        worker_job = self._build_worker_job(job, source_image_path)
        self.storage.save_worker_job(job_id, worker_job)

        self._launch_worker(job_id, sync=bool(self._worker_runner))
        if self._worker_runner is not None:
            self._finalize_job(job_id)
        self._ensure_orchestrator()
        return self.storage.load_job(job_id)

    # ------------------------------------------------------------------ artifacts
    def get_artifact(self, artifact_id: str) -> dict:
        validate_uuid(artifact_id, "artifact", VideoGenerationValidationError)
        return self.storage.load_artifact(artifact_id)

    def list_artifacts(self) -> list[dict]:
        artifacts = list(self.storage.iter_artifacts())
        artifacts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        return artifacts

    # ------------------------------------------------------------------ lifecycle
    def shutdown(self) -> None:
        """Stop the orchestrator and terminate active workers."""
        self._orchestrator_stop.set()
        with self._lock:
            procs = list(self._active.values())
        for proc in procs:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=KILL_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:  # pragma: no cover - defensive
                pass
        with self._lock:
            self._active.clear()

    # ------------------------------------------------------------------ helpers
    def _check_dependencies(self) -> tuple[bool, Optional[str]]:
        try:
            import torch  # noqa: F401
        except ImportError:
            return False, "torch is not installed (see requirements-gpu.txt)"
        try:
            import diffusers  # noqa: F401
        except ImportError:
            return False, "diffusers is not installed (see requirements-gpu.txt)"
        return True, None

    def _check_cuda_available(self) -> bool:
        try:
            import torch  # noqa: F401

            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _check_worker_module(self) -> bool:
        try:
            import importlib

            importlib.import_module("ttvturbo.video_generation.worker")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ validation
    def _validate_request(
        self,
        *,
        generation_type: str,
        prompt: str,
        source_image_asset_id: Optional[str],
        duration_seconds: float,
        aspect_ratio: str,
        seed: Optional[int],
        options: Optional[dict],
    ) -> dict:
        if generation_type not in SUPPORTED_GENERATION_TYPES:
            raise VideoGenerationValidationError(
                f"unsupported generation type {generation_type!r}; "
                f"supported: {sorted(SUPPORTED_GENERATION_TYPES)}"
            )
        if not isinstance(prompt, str) or not prompt.strip():
            raise VideoGenerationValidationError("prompt must be a non-empty string")
        max_prompt = self.settings.video_generation_max_prompt_length
        if len(prompt) > max_prompt:
            raise VideoGenerationValidationError(
                f"prompt length {len(prompt)} exceeds maximum {max_prompt}"
            )
        if aspect_ratio not in ASPECT_RATIOS:
            raise VideoGenerationValidationError(
                f"unknown aspect_ratio {aspect_ratio!r}; "
                f"expected one of {sorted(ASPECT_RATIOS)}"
            )
        max_duration = self.settings.video_generation_max_duration_seconds
        if duration_seconds <= 0:
            raise VideoGenerationValidationError(
                f"duration_seconds must be > 0, got {duration_seconds}"
            )
        if duration_seconds > max_duration:
            raise VideoGenerationValidationError(
                f"duration_seconds {duration_seconds} exceeds maximum {max_duration}"
            )
        if seed is not None:
            if not isinstance(seed, int) or isinstance(seed, bool):
                raise VideoGenerationValidationError(
                    "seed must be an integer or null"
                )
            if seed < 0:
                raise VideoGenerationValidationError(
                    f"seed must be >= 0, got {seed}"
                )
        if options is not None and not isinstance(options, dict):
            raise VideoGenerationValidationError("options must be an object")

        if generation_type == "IMAGE_TO_VIDEO":
            if not source_image_asset_id or not isinstance(source_image_asset_id, str):
                raise VideoGenerationValidationError(
                    "IMAGE_TO_VIDEO requires a source_image_asset_id"
                )
        else:  # TEXT_TO_VIDEO
            if source_image_asset_id is not None:
                raise VideoGenerationValidationError(
                    "TEXT_TO_VIDEO must not provide a source_image_asset_id"
                )

        resolution = resolution_for_aspect_ratio(aspect_ratio)
        fps = self.settings.video_generation_fps or DEFAULT_FPS
        effective = self._build_effective_options(options)
        return {
            "generation_type": generation_type,
            "prompt": prompt,
            "source_image_asset_id": source_image_asset_id,
            "duration_seconds": float(duration_seconds),
            "aspect_ratio": aspect_ratio,
            "seed": seed,
            "options": dict(options or {}),
            "effective_options": effective,
            "resolution": [int(resolution[0]), int(resolution[1])],
            "fps": int(fps),
        }

    def _build_effective_options(self, options: Optional[dict]) -> dict:
        """Filter free-form options down to the whitelisted, bounded set."""
        raw = options or {}
        try:
            model = EffectiveOptions(
                num_frames=int(raw.get("num_frames", 49)),
                guidance_scale=float(raw.get("guidance_scale", 6.0)),
                num_inference_steps=int(raw.get("num_inference_steps", 50)),
                negative_prompt=str(raw.get("negative_prompt", "") or ""),
            )
        except Exception as exc:
            raise VideoGenerationValidationError(
                f"invalid options: {exc}"
            ) from exc
        return model.model_dump()

    # ------------------------------------------------------------------ source image
    def _copy_source_image(self, job_id: str, asset_id: str) -> str:
        """Resolve a library image asset and copy it into the job dir.

        Returns the absolute path of the copied image inside the job
        directory.  Only library asset ids are accepted -- never free
        file paths.
        """
        validate_uuid(asset_id, "source_image_asset_id", VideoGenerationValidationError)
        if self.library_service is None:
            raise VideoGenerationUnavailableError(
                "library service is not configured; cannot resolve source image"
            )
        try:
            file_path = self.library_service.item_file_path(asset_id)
        except Exception as exc:
            raise VideoGenerationValidationError(
                f"source image asset not found: {asset_id}"
            ) from exc
        if not file_path.is_file():
            raise VideoGenerationValidationError(
                f"source image asset file is missing: {asset_id}"
            )
        ext = file_path.suffix.lower() or ".png"
        if ext.lstrip(".") not in {"png", "jpg", "jpeg", "webp", "bmp"}:
            raise VideoGenerationValidationError(
                f"source image must be an image file, got {ext}"
            )
        dest = self.storage._job_dir(job_id) / f"source_image{ext}"  # noqa: SLF001
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(file_path), str(dest))
        return str(dest)

    # ------------------------------------------------------------------ worker
    def _build_worker_job(self, job: dict, source_image_path: Optional[str]) -> dict:
        model_id = (job.get("model") or {}).get("model_id") or ""
        return {
            "job_dir": str(self.storage._job_dir(job["id"])),  # noqa: SLF001
            "type": job["type"],
            "model_id": model_id,
            "device": self.settings.video_generation_device,
            "dtype": self.settings.video_generation_dtype,
            "model_cache_dir": self.settings.asr_model_cache_dir,
            "gpu_lock_data_dir": str(self.settings.paths().data_root),
            "gpu_lock_stale_seconds": self.settings.gpu_lock_stale_seconds,
            "prompt": job["prompt"],
            "seed": job.get("seed"),
            "aspect_ratio": job["aspect_ratio"],
            "resolution": job["resolution"],
            "fps": job["fps"],
            "duration_seconds": job["duration_seconds"],
            "effective_options": job["effective_options"],
            "source_image_path": source_image_path,
        }

    def _launch_worker(self, job_id: str, *, sync: bool) -> None:
        if self._worker_runner is not None:
            # Synchronous runner (tests).  The runner is responsible for
            # updating job.json + writing result.json + output.mp4.
            worker_job_path = self.storage.worker_job_path(job_id)
            import json as _json

            with open(worker_job_path, "r", encoding="utf-8-sig") as fh:
                wjob = _json.load(fh)
            job_dir = self.storage._job_dir(job_id)  # noqa: SLF001
            try:
                self._worker_runner(wjob, job_dir)
            except Exception as exc:
                # Treat a runner crash as a job failure.
                try:
                    job = self.storage.load_job(job_id)
                    self._fail_job(job, exc)
                except Exception:
                    pass
            return

        # Spawn the real worker subprocess.
        cmd = [
            self._worker_python,
            "-m",
            "ttvturbo.video_generation.worker",
            str(self.storage.worker_job_path(job_id)),
        ]
        log_fh = open(self.storage.worker_log_path(job_id), "ab")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            log_fh.close()
            try:
                job = self.storage.load_job(job_id)
                self._fail_job(job, exc)
            except Exception:
                pass
            raise VideoGenerationError(f"could not start worker: {exc}") from exc
        with self._lock:
            self._active[job_id] = proc
        try:
            log_fh.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ orchestrator
    def _ensure_orchestrator(self) -> None:
        with self._lock:
            if self._orchestrator_thread is not None and self._orchestrator_thread.is_alive():
                return
            self._orchestrator_stop.clear()
            t = threading.Thread(
                target=self._orchestrator_loop,
                daemon=True,
                name="video-generation-orchestrator",
            )
            self._orchestrator_thread = t
            t.start()

    def _orchestrator_loop(self) -> None:
        while not self._orchestrator_stop.is_set():
            try:
                self._advance_jobs()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("video-generation orchestrator iteration failed: %s", exc)
            active = any(
                j.get("status") in (VideoGenerationJobStatus.QUEUED, VideoGenerationJobStatus.RUNNING)
                for j in self.storage.iter_jobs()
            )
            if not active:
                self._orchestrator_stop.set()
                break
            time.sleep(ORCHESTRATOR_POLL_SECONDS)

    def _advance_jobs(self) -> None:
        for job in list(self.storage.iter_jobs()):
            jid = job.get("id")
            if not jid:
                continue
            if job.get("status") not in (
                VideoGenerationJobStatus.QUEUED,
                VideoGenerationJobStatus.RUNNING,
            ):
                continue
            try:
                self._advance_job(jid)
            except Exception as exc:
                logger.warning("advance video-generation job %s failed: %s", jid, exc)

    def _advance_job(self, job_id: str) -> None:
        job = self.storage.load_job(job_id)
        # Handle cancel: the worker is terminated by cancel_job; just
        # reconcile state.
        if job.get("status") == VideoGenerationJobStatus.CANCELED:
            with self._lock:
                proc = self._active.pop(job_id, None)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            return

        with self._lock:
            proc = self._active.get(job_id)
        worker_alive = proc is not None and proc.poll() is None

        if worker_alive:
            return  # still running

        # Worker has exited (or was never tracked for the sync path).
        # Reload the latest state written by the worker.
        job = self.storage.load_job(job_id)
        status = job.get("status")
        if status == VideoGenerationJobStatus.COMPLETED:
            with self._lock:
                self._active.pop(job_id, None)
            self._finalize_job(job_id)
        elif status in (VideoGenerationJobStatus.QUEUED, VideoGenerationJobStatus.RUNNING):
            # Worker exited without marking the job terminal -> failure.
            with self._lock:
                self._active.pop(job_id, None)
            result = self.storage.load_result(job_id)
            message = "worker exited without completing the job"
            if result and result.get("error"):
                message = result["error"]
            self._fail_job(job, VideoGenerationError(message))

    # ------------------------------------------------------------------ finalize
    def _finalize_job(self, job_id: str) -> None:
        """Register the library item + artifact for a completed job.

        Idempotent: if the job already has an ``output_artifact_id`` the
        call is a no-op.  A FAILED job is never finalized.
        """
        try:
            job = self.storage.load_job(job_id)
        except VideoGenerationNotFoundError:
            return
        if job.get("status") != VideoGenerationJobStatus.COMPLETED:
            return
        if job.get("output_artifact_id"):
            return  # already finalized

        result = self.storage.load_result(job_id)
        if result is None or not result.get("success"):
            self._fail_job(job, VideoGenerationError("worker result is missing or unsuccessful"))
            return

        # Validate the worker result strictly.
        try:
            validated = GenerationResult.model_validate(result)
        except Exception as exc:
            self._fail_job(job, VideoGenerationValidationError(f"invalid worker result: {exc}"))
            return

        output_path = self.storage.output_path(job_id)
        if not output_path.is_file():
            self._fail_job(job, VideoGenerationError("generated output file is missing"))
            return

        # Move the output into the persistent library.
        library_item_id = self._register_library_item(job, validated, output_path)
        if library_item_id is None:
            # Without a library we cannot register a final artifact.
            self._fail_job(job, VideoGenerationUnavailableError("library service is not configured"))
            return

        artifact_id = _new_uuid()
        now = _now_iso()
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "id": artifact_id,
            "job_id": job_id,
            "generation_type": job["type"],
            "model_id": validated.model_id,
            "model_revision": validated.model_revision,
            "prompt": validated.prompt,
            "seed": validated.seed,
            "effective_options": validated.effective_options,
            "duration_seconds": validated.duration_seconds,
            "resolution": validated.resolution,
            "fps": validated.fps,
            "library_item_id": library_item_id,
            "file_name": "source.mp4",
            "file_size_bytes": validated.file_size_bytes,
            "container": "mp4",
            "source_image_asset_id": job.get("source_image_asset_id"),
            "created_at": now,
            "revision": 1,
        }
        # Validate via the pydantic model before persisting.
        VideoGenerationArtifact.model_validate(artifact)
        self.storage.save_artifact(artifact)

        job = self.storage.load_job(job_id)
        job["output_artifact_id"] = artifact_id
        job["library_item_id"] = library_item_id
        job["model"] = {
            "model_id": validated.model_id,
            "model_revision": validated.model_revision,
        }
        job["resolution"] = validated.resolution
        job["fps"] = validated.fps
        job["duration_seconds"] = validated.duration_seconds
        self.storage.save_job(job)

        # Register a back-reference on the library item (best-effort).
        if self.library_service is not None:
            try:
                self._register_library_artifact_reference(library_item_id, artifact)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("library artifact back-reference failed: %s", exc)

    def _register_library_item(
        self,
        job: dict,
        result: GenerationResult,
        output_path: Path,
    ) -> Optional[str]:
        """Move the generated video into the library and return its id."""
        if self.library_service is None:
            return None
        # Library items store their file under the canonical
        # ``source.{container}`` name (see LibraryStorage.source_file_path).
        # We record that canonical name on the metadata so
        # ``item_file_path`` can locate it.
        canonical_name = "source.mp4"
        title = f"Generated video ({job['type']})"
        meta = self.library_service.create_upload_item(
            file_name=canonical_name,
            title=title,
            duration_seconds=result.duration_seconds,
        )
        item_id = meta["id"]
        dest = self.library_service.storage.source_file_path(item_id, "mp4")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(output_path), str(dest))
        except (OSError, shutil.Error) as exc:
            raise VideoGenerationStorageError(
                f"could not move generated video into library: {exc}"
            ) from exc
        meta["file_size_bytes"] = dest.stat().st_size
        meta["duration_seconds"] = result.duration_seconds
        meta["file_name"] = canonical_name
        # Tag the library item so consumers can identify generated videos.
        meta["generated"] = True
        meta["generation"] = {
            "job_id": job["id"],
            "generation_type": job["type"],
            "model_id": result.model_id,
            "model_revision": result.model_revision,
            "prompt": result.prompt,
            "seed": result.seed,
            "artifact_id": None,  # filled by the back-reference step
        }
        self.library_service.storage.save_item(meta)
        return item_id

    def _register_library_artifact_reference(self, library_item_id: str, artifact: dict) -> None:
        meta = self.library_service.get_item(library_item_id)
        artifacts = meta.setdefault("artifacts", [])
        artifacts.append({
            "artifact_id": artifact["id"],
            "artifact_type": ARTIFACT_TYPE,
            "created_at": artifact["created_at"],
            "revision": str(artifact.get("revision", 1)),
        })
        # Back-fill the artifact id on the generation tag.
        gen = meta.get("generation") or {}
        if gen and not gen.get("artifact_id"):
            gen["artifact_id"] = artifact["id"]
            meta["generation"] = gen
        meta["updated_at"] = _now_iso()
        self.library_service.storage.save_item(meta)

    # ------------------------------------------------------------------ failure / recovery
    def _fail_job(self, job: dict, exc: Exception) -> None:
        job["status"] = VideoGenerationJobStatus.FAILED
        job["completed_at"] = _now_iso()
        job["current_stage"] = None
        job["error"] = {
            "code": _error_code(exc),
            "message": str(exc),
            "retryable": isinstance(exc, VideoGenerationUnavailableError),
        }
        try:
            self.storage.save_job(job)
        except Exception:  # pragma: no cover - defensive
            pass

    def _recover_on_startup(self) -> None:
        """Recover active jobs after a server restart.

        Any job left in QUEUED or RUNNING whose worker process is gone
        (always true on a fresh start) is marked FAILED so the user can
        retry it explicitly.
        """
        for job in list(self.storage.iter_jobs()):
            if job.get("status") not in (
                VideoGenerationJobStatus.QUEUED,
                VideoGenerationJobStatus.RUNNING,
            ):
                continue
            job["status"] = VideoGenerationJobStatus.FAILED
            job["error"] = {
                "code": "VG_RECOVERY",
                "message": "server restarted while job was active",
                "retryable": True,
            }
            job["completed_at"] = _now_iso()
            job["current_stage"] = None
            try:
                self.storage.save_job(job)
            except Exception:  # pragma: no cover - defensive
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_code(exc: Exception) -> str:
    if isinstance(exc, VideoGenerationValidationError):
        return "VG_VALIDATION"
    if isinstance(exc, VideoGenerationUnavailableError):
        return "VG_UNAVAILABLE"
    if isinstance(exc, VideoGenerationConflictError):
        return "VG_CONFLICT"
    if isinstance(exc, VideoGenerationNotFoundError):
        return "VG_NOT_FOUND"
    if isinstance(exc, VideoGenerationStorageError):
        return "VG_STORAGE"
    return "VG_INTERNAL"
