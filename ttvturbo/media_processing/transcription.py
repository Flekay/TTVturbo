"""Reusable transcription service (faster-whisper).

Orchestrates a separate Python worker subprocess
(:mod:`media_processing.transcription_worker`) that loads faster-whisper
and transcribes a ready audio artifact. The FastAPI process stays
responsive during multi-hour VODs.

Key properties:

* the worker runs in a separate process so the model load (which can
  take several seconds and several GB of VRAM) never blocks the API;
* the worker acquires the project-wide :class:`GpuLock` before loading
  the model — voice-clone and transcription never load models
  simultaneously;
* if ``device=cuda`` is configured but CUDA is unavailable, the job is
  reported as unavailable (no silent CPU fallback, no multi-hour CPU
  job);
* the transcript is persisted under
  ``vods/{vod_id}/artifacts/transcripts/{transcription_id}/`` with
  ``metadata.json``, ``transcript.json``, ``transcript.txt``,
  ``transcript.srt`` and ``transcript.vtt``;
* only READY transcript files are served; ``.part`` files are never
  exposed.

The service is reused by:

* the on-demand Transcription page;
* the VOD Pipeline orchestration.

No download logic lives here. The audio must already be a ready artifact
produced by :class:`media_processing.audio_extraction.AudioExtractionService`.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .audio_extraction import AudioExtractionService
from .asr_presets import AsrDefaultPresetStore, AsrPreset, get_preset
from .gpu_lock import GpuLock, GpuLockBusyError, GpuLockError, GpuLockOwner
from .schemas import (
    CANCELLABLE_JOB_STATUSES,
    JobType,
    MediaJobConflictError,
    MediaJobNotFoundError,
    MediaJobStatus,
    MediaJobStorageError,
    MediaJobValidationError,
    MediaSourceError,
    MediaSourceNotFoundError,
    MediaSourceNotReadyError,
    RETRYABLE_JOB_STATUSES,
    SCHEMA_VERSION,
    TRANSIENT_JOB_STATUSES,
    TranscriptionStatus,
)
from .sources import MediaSourceResolver
from .storage import MediaJobStorage

logger = logging.getLogger("ttvturbo.media_processing.transcription")

ARTIFACTS_SUBDIR = "artifacts"
TRANSCRIPTS_SUBDIR = "transcripts"
TRANSCRIPT_JSON = "transcript.json"
TRANSCRIPT_TXT = "transcript.txt"
TRANSCRIPT_SRT = "transcript.srt"
TRANSCRIPT_VTT = "transcript.vtt"
TRANSCRIPT_METADATA = "metadata.json"
TRANSCRIPT_PART_SUFFIX = ".part"

KILL_GRACE_SECONDS = 5.0

# Environment-variable-driven configuration. All overridable.
ENV_MODEL = "TTVTURBO_TRANSCRIPTION_MODEL"
ENV_DEVICE = "TTVTURBO_TRANSCRIPTION_DEVICE"
ENV_COMPUTE_TYPE = "TTVTURBO_TRANSCRIPTION_COMPUTE_TYPE"
ENV_LANGUAGE = "TTVTURBO_TRANSCRIPTION_LANGUAGE"
ENV_MAX_CONCURRENT = "TTVTURBO_MAX_CONCURRENT_TRANSCRIPTIONS"

DEFAULT_MODEL = "large-v3"
DEFAULT_DEVICE = "cuda"
DEFAULT_COMPUTE_TYPE = "int8_float16"
DEFAULT_LANGUAGE = "de"
DEFAULT_MAX_CONCURRENT = 1


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


class TranscriptionError(Exception):
    """Transcription-specific error."""


class TranscriptionService:
    """Service that transcribes a ready audio artifact via faster-whisper."""

    def __init__(
        self,
        storage: MediaJobStorage,
        source_resolver: MediaSourceResolver,
        audio_service: AudioExtractionService,
        gpu_lock: GpuLock,
        model: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        language: Optional[str] = None,
        max_concurrent: Optional[int] = None,
        default_preset_store: Optional[AsrDefaultPresetStore] = None,
    ) -> None:
        self.storage = storage
        self.source_resolver = source_resolver
        self.audio_service = audio_service
        self.gpu_lock = gpu_lock
        self.default_preset_store = default_preset_store
        # Resolve configuration from explicit params, then central Settings,
        # then module defaults.  Services never interpret env vars directly.
        from ttvturbo.settings import Settings

        _s = Settings.from_env()
        self.model = model or _s.transcription_model
        self.device = device or _s.transcription_device
        self.compute_type = compute_type or _s.transcription_compute_type
        self.language = language or _s.transcription_language
        mc = max_concurrent if max_concurrent is not None else _s.transcription_max_concurrent
        self.max_concurrent = max(1, int(mc))

        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen] = {}
        self._active_log_fh: dict[str, Any] = {}
        self._runtime_cache: Optional[dict] = None
        self._runtime_ts: float = 0.0
        self._runtime_ttl = 5.0

        self._recover_on_startup()

    # ------------------------------------------------------------------ preset
    def _resolve_effective_preset(
        self, language: Optional[str], model: Optional[str]
    ) -> tuple[Optional[AsrPreset], str, str]:
        """Resolve the effective ASR parameters for a new transcription.

        Priority (highest first):
          1. explicit per-request ``language`` / ``model`` overrides;
          2. the production default preset from :class:`AsrDefaultPresetStore`
             (if configured);
          3. the service's env-var / constructor defaults.

        Returns ``(preset_or_None, effective_language, effective_model)``.
        When a preset is active, its ``language`` and ``model`` fields
        override the service defaults unless the caller passed explicit
        values. ``preset`` is ``None`` when no preset store is configured
        (legacy behaviour).
        """
        preset: Optional[AsrPreset] = None
        if self.default_preset_store is not None:
            try:
                selection = self.default_preset_store.get()
                if selection and selection.get("preset_id"):
                    preset = get_preset(selection["preset_id"])
            except Exception as exc:
                logger.warning(
                    "could not load default ASR preset, falling back to "
                    "service defaults: %s", exc,
                )
                preset = None

        if preset is not None:
            eff_lang = language or preset.language or self.language
            eff_model = model or preset.model or self.model
        else:
            eff_lang = language or self.language
            eff_model = model or self.model
        return preset, eff_lang, eff_model

    # ------------------------------------------------------------------ paths
    def _source_dir_for(self, source_id: str, source_type: Optional[str] = None) -> Path:
        """Resolve the source directory for a source_id that may be either a
        twitch_vod or a file_upload. When ``source_type`` is known, use it
        directly; otherwise try VOD storage first, then uploads.
        """
        if source_type == "file_upload":
            return self.source_resolver.get_source_dir("file_upload", source_id)
        if source_type == "twitch_vod":
            return self.source_resolver.get_vod_dir(source_id)
        try:
            return self.source_resolver.get_vod_dir(source_id)
        except MediaSourceNotFoundError:
            pass
        if self.source_resolver.upload_storage is not None or self.source_resolver.library_service is not None:
            try:
                return self.source_resolver.get_source_dir("file_upload", source_id)
            except MediaSourceError:
                pass
        raise MediaSourceNotFoundError(f"source not found: {source_id}")

    def transcripts_dir(self, vod_id: str, source_type: Optional[str] = None) -> Path:
        source_dir = self._source_dir_for(vod_id, source_type)
        return source_dir / ARTIFACTS_SUBDIR / TRANSCRIPTS_SUBDIR

    def transcript_dir(self, vod_id: str, transcription_id: str, source_type: Optional[str] = None) -> Path:
        # Validate transcription_id as UUID to prevent path traversal.
        try:
            uuid.UUID(transcription_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise MediaJobValidationError(f"invalid transcription id: {transcription_id!r}") from exc
        return self.transcripts_dir(vod_id, source_type) / transcription_id

    # ------------------------------------------------------------------ runtime
    def runtime_status(self) -> dict:
        """Return the runtime availability status without loading the model.

        Caches for ``_runtime_ttl`` seconds. Probes faster-whisper
        importability and CUDA availability (when device=cuda) only.
        """
        now = time.monotonic()
        cached = self._runtime_cache
        if cached is not None and (now - self._runtime_ts) < self._runtime_ttl:
            return dict(cached)

        reasons: list[str] = []
        warnings: list[str] = []
        device_name: Optional[str] = None
        model_cached = False

        faster_whisper_importable = False
        try:
            import faster_whisper  # type: ignore[import-not-found]  # noqa: F401

            faster_whisper_importable = True
        except Exception as exc:
            reasons.append(
                f"faster-whisper is not installed: {type(exc).__name__}: {exc}"
            )

        # Check if the model is already cached in the HuggingFace hub cache.
        if faster_whisper_importable:
            try:
                from huggingface_hub import scan_cache_dir  # type: ignore[import-not-found]

                cache_info = scan_cache_dir()
                for repo in cache_info.repos:
                    if f"faster-whisper-{self.model}" in repo.repo_id:
                        model_cached = True
                        break
            except Exception:
                pass

        cuda_available = False
        if self.device.startswith("cuda"):
            try:
                import torch  # type: ignore[import-not-found]

                if getattr(torch.version, "cuda", None) is None:
                    reasons.append(
                        "PyTorch was installed without CUDA support "
                        "(torch.version.cuda is None)."
                    )
                else:
                    cuda_available = bool(torch.cuda.is_available())
                    if not cuda_available:
                        reasons.append("torch.cuda.is_available() is False.")
                    else:
                        try:
                            idx = 0
                            if self.device.startswith("cuda:"):
                                try:
                                    idx = int(self.device.split(":", 1)[1])
                                except ValueError:
                                    idx = 0
                            device_name = torch.cuda.get_device_name(idx)
                        except Exception as exc:  # pragma: no cover
                            warnings.append(f"Could not read CUDA device name: {exc}")
            except Exception as exc:
                reasons.append(f"torch is not importable: {type(exc).__name__}: {exc}")
        elif self.device == "cpu":
            # CPU is only allowed when explicitly configured.
            pass
        else:
            reasons.append(f"unsupported device {self.device!r}")

        # GPU lock state.
        owner = self.gpu_lock.current_owner()
        busy = owner is not None
        busy_owner_type = owner.get("owner_type") if owner else None

        available = faster_whisper_importable and (cuda_available if self.device.startswith("cuda") else True) and not busy

        payload = {
            "available": bool(available),
            "busy": bool(busy),
            "busy_owner_type": busy_owner_type,
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "device_name": device_name,
            "model_cached": model_cached,
            "faster_whisper_importable": faster_whisper_importable,
            "cuda_available": cuda_available,
            "reasons": reasons,
            "warnings": warnings,
        }
        self._runtime_cache = payload
        self._runtime_ts = now
        return payload

    # ------------------------------------------------------------------ model preload
    def preload_model(self) -> dict:
        """Pre-download the configured faster-whisper model from HuggingFace
        Hub into the local cache so the first transcription job does not
        block on a multi-GB download.

        Returns a dict with ``ok``, ``model``, ``repo_id`` and optional
        ``error`` keys. This method is synchronous and may take a while;
        the API endpoint runs it in a thread so the FastAPI event loop
        stays responsive.
        """
        repo_id = (
            f"Systran/faster-whisper-{self.model}"
            if not self.model.startswith("/")
            else self.model
        )
        try:
            from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
        except Exception as exc:
            return {
                "ok": False,
                "model": self.model,
                "repo_id": repo_id,
                "error": f"huggingface_hub not importable: {type(exc).__name__}: {exc}",
            }
        try:
            snapshot_download(repo_id=repo_id, local_files_only=False)
        except Exception as exc:
            return {
                "ok": False,
                "model": self.model,
                "repo_id": repo_id,
                "error": f"download failed: {type(exc).__name__}: {exc}",
            }
        # Invalidate the runtime cache so the next status probe reflects the
        # newly cached model.
        self._runtime_cache = None
        return {
            "ok": True,
            "model": self.model,
            "repo_id": repo_id,
        }

    # ------------------------------------------------------------------ transcripts
    def list_transcriptions(self, vod_id: Optional[str] = None) -> list[dict]:
        """List persisted transcription records (metadata.json under each
        transcript dir). Optionally filtered by vod_id.
        """
        out: list[dict] = []
        if vod_id:
            tdir = self.transcripts_dir(vod_id)
            if tdir.is_dir():
                for sub in tdir.iterdir():
                    if not sub.is_dir():
                        continue
                    meta = sub / TRANSCRIPT_METADATA
                    if meta.is_file():
                        rec = self._read_meta(meta)
                        if rec is not None:
                            out.append(rec)
        else:
            # Iterate all source roots (VODs + uploads + library) that have transcripts.
            source_roots: list[Path] = []
            vods_root = self.source_resolver.vod_storage.vods_dir
            if vods_root.is_dir():
                source_roots.append(vods_root)
            if self.source_resolver.upload_storage is not None:
                uploads_root = self.source_resolver.upload_storage.uploads_dir
                if uploads_root.is_dir():
                    source_roots.append(uploads_root)
            if self.source_resolver.library_service is not None:
                library_root = self.source_resolver.library_service.storage.library_dir
                if library_root.is_dir():
                    source_roots.append(library_root)
            for root in source_roots:
                for src_dir in root.iterdir():
                    if not src_dir.is_dir():
                        continue
                    tdir = src_dir / ARTIFACTS_SUBDIR / TRANSCRIPTS_SUBDIR
                    if not tdir.is_dir():
                        continue
                    for sub in tdir.iterdir():
                        if not sub.is_dir():
                            continue
                        meta = sub / TRANSCRIPT_METADATA
                        if meta.is_file():
                            rec = self._read_meta(meta)
                            if rec is not None:
                                out.append(rec)
        out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return out

    def _read_meta(self, path: Path) -> Optional[dict]:
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def get_transcription(self, transcription_id: str) -> dict:
        """Return a transcription record by id (searches all VODs)."""
        for rec in self.list_transcriptions():
            if rec.get("id") == transcription_id:
                return rec
        raise MediaJobNotFoundError(f"transcription not found: {transcription_id}")

    def _find_transcription_dir(self, transcription_id: str) -> Path:
        for rec in self.list_transcriptions():
            if rec.get("id") == transcription_id:
                vod_id = rec.get("source_id")
                stype = rec.get("source_type")
                if vod_id:
                    return self.transcript_dir(vod_id, transcription_id, stype)
        raise MediaJobNotFoundError(f"transcription not found: {transcription_id}")

    # ------------------------------------------------------------------ start
    def start_transcription(
        self,
        source_type: str,
        source_id: str,
        language: Optional[str] = None,
        model: Optional[str] = None,
        model_family: Optional[str] = None,
        hotwords: Optional[str] = None,
        force_audio_extraction: bool = False,
    ) -> dict:
        """Start a transcription for ``(source_type, source_id)``.

        If no ready audio artifact exists, an EXTRACT_AUDIO job is started
        first and the TRANSCRIBE job is created as WAITING_FOR_DEPENDENCY.
        Once the audio job becomes READY, the transcription service
        automatically starts the transcription worker (the audio worker
        calls :meth:`on_audio_ready` via the pipeline, or the API layer
        polls and triggers it).

        Returns the TRANSCRIBE job record.
        """
        if source_type not in ("twitch_vod", "file_upload"):
            raise MediaSourceError(f"unsupported source_type {source_type!r}")
        # Verify the source exists and is READY.
        self.source_resolver.resolve(source_type, source_id)

        # NOTE: we do NOT block job creation on runtime availability here.
        # The job is created and queued; the worker will fail with a clear
        # error if faster-whisper is missing or CUDA is unavailable. This
        # lets the user queue transcriptions that will run once the runtime
        # is available, and it keeps tests simple (no model required to
        # test job creation and dependency wiring).

        # Reuse an existing READY transcription with the same source and
        # options, unless the user explicitly wants a new one. We treat
        # each start as a new version for simplicity in this phase, but
        # we expose the existing ones via list_transcriptions.
        preset, effective_language, effective_model = self._resolve_effective_preset(
            language, model
        )

        # Ensure audio artifact exists (start extraction if needed).
        audio_meta = self.audio_service.get_audio_artifact(source_id, source_type)
        audio_job_id: Optional[str] = None
        if audio_meta is None or force_audio_extraction:
            # Start audio extraction. This raises if the source is not READY.
            audio_job = self.audio_service.start_extraction(
                source_type, source_id, force=force_audio_extraction,
            )
            audio_job_id = audio_job.get("id")
            audio_status = audio_job.get("status")
        else:
            audio_status = MediaJobStatus.READY.value

        # Create the TRANSCRIBE job.
        job_id = _new_uuid()
        transcription_id = _new_uuid()
        now = _now_iso()
        if audio_status == MediaJobStatus.READY.value:
            status = MediaJobStatus.WAITING_FOR_GPU.value
        else:
            status = MediaJobStatus.WAITING_FOR_DEPENDENCY.value
        job = {
            "schema_version": SCHEMA_VERSION,
            "id": job_id,
            "job_type": JobType.TRANSCRIBE.value,
            "source_type": source_type,
            "source_id": source_id,
            "status": status,
            "progress": {"percent": None, "processed_seconds": None, "total_seconds": None, "phase": None},
            "options": {
                "language": effective_language,
                "model": effective_model,
                "model_family": model_family or "whisper",
                "hotwords": (hotwords or "").strip() or None,
                "force_audio_extraction": bool(force_audio_extraction),
                "preset_id": preset.id if preset else None,
                "preset_params": preset.to_dict() if preset else None,
            },
            "result": None,
            "error": None,
            "depends_on": audio_job_id,
            "transcription_id": transcription_id,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now,
        }
        self.storage.save_job(job)

        # If audio is already ready, start the worker now.
        if status == MediaJobStatus.WAITING_FOR_GPU.value:
            self._try_start_worker(job_id)
        return self.storage.load_job(job_id)

    def on_audio_ready(self, audio_job_id: str) -> None:
        """Called when an EXTRACT_AUDIO job becomes READY.

        Finds any TRANSCRIBE job waiting on it and starts the worker.
        """
        audio_job = self.storage.load_job(audio_job_id)
        source_id = audio_job.get("source_id")
        if not source_id:
            return
        for job in self.storage.iter_jobs():
            if job.get("job_type") != JobType.TRANSCRIBE.value:
                continue
            if job.get("depends_on") != audio_job_id:
                continue
            if job.get("status") == MediaJobStatus.WAITING_FOR_DEPENDENCY.value:
                # Transition to WAITING_FOR_GPU and try to start.
                job["status"] = MediaJobStatus.WAITING_FOR_GPU.value
                job["updated_at"] = _now_iso()
                self.storage.save_job(job)
                self._try_start_worker(job["id"])

    def poll_dependencies(self) -> None:
        """Check all WAITING_FOR_DEPENDENCY transcribe jobs and transition
        them if their audio dependency has become READY.

        This is the safety net that complements :meth:`on_audio_ready`.
        The audio extraction worker writes READY to the job file directly;
        the FastAPI process may not observe the subprocess exit immediately.
        The API layer calls this on each transcription list/get request so
        stuck jobs recover within a few seconds without manual intervention.
        """
        for job in self.storage.iter_jobs():
            if job.get("job_type") != JobType.TRANSCRIBE.value:
                continue
            if job.get("status") != MediaJobStatus.WAITING_FOR_DEPENDENCY.value:
                continue
            dep_id = job.get("depends_on")
            if not dep_id:
                # No dependency recorded — treat as ready to schedule.
                job["status"] = MediaJobStatus.WAITING_FOR_GPU.value
                job["updated_at"] = _now_iso()
                self.storage.save_job(job)
                self._try_start_worker(job["id"])
                continue
            try:
                dep_job = self.storage.load_job(dep_id)
            except MediaJobStorageError:
                # Dependency job file is gone — mark failed.
                self._mark_failed(job["id"], "audio extraction job was deleted before completion.")
                continue
            dep_status = dep_job.get("status")
            if dep_status == MediaJobStatus.READY.value:
                job["status"] = MediaJobStatus.WAITING_FOR_GPU.value
                job["updated_at"] = _now_iso()
                self.storage.save_job(job)
                self._try_start_worker(job["id"])
            elif dep_status == MediaJobStatus.FAILED.value:
                self._mark_failed(
                    job["id"],
                    f"audio extraction failed: {dep_job.get('error') or 'unknown error'}",
                )
            elif dep_status == MediaJobStatus.CANCELED.value:
                self._mark_failed(job["id"], "audio extraction was canceled.")

    def _try_start_worker(self, job_id: str) -> None:
        """Attempt to start the transcription worker if a slot is free."""
        with self._lock:
            # Reap dead.
            dead = [jid for jid, p in self._active.items() if p.poll() is not None]
            for jid in dead:
                self._active.pop(jid, None)
                self._active_log_fh.pop(jid, None)
            if len(self._active) >= self.max_concurrent:
                # Stay WAITING_FOR_GPU.
                return
        # Check the GPU lock. If busy, stay WAITING_FOR_GPU.
        owner = self.gpu_lock.current_owner()
        if owner is not None:
            # If the owner is us (a transcription job), that's a bug — but
            # we still wait rather than fail.
            return
        self._spawn_worker(job_id)

    def _spawn_worker(self, job_id: str) -> None:
        job = self.storage.load_job(job_id)
        source_id = job.get("source_id")
        transcription_id = job.get("transcription_id")
        if not source_id or not transcription_id:
            self._mark_failed(job_id, "job is missing source_id or transcription_id")
            return
        # Resolve the audio artifact path.
        source_type = job.get("source_type", "twitch_vod")
        audio_meta = self.audio_service.get_audio_artifact(source_id, source_type)
        if audio_meta is None:
            self._mark_failed(job_id, "audio artifact is missing")
            return
        audio_path = self.audio_service.artifact_path(source_id, source_type)
        if not audio_path.is_file():
            self._mark_failed(job_id, "audio artifact file is missing on disk")
            return

        transcript_dir = self.transcript_dir(source_id, transcription_id, source_type)
        transcript_dir.mkdir(parents=True, exist_ok=True)

        options = job.get("options") or {}
        # When a preset was selected for this job, forward the full preset
        # parameters so the worker uses them instead of its hardcoded
        # defaults. This is what makes the benchmark-selected preset
        # actually take effect in production.
        preset_params = options.get("preset_params")
        if preset_params and isinstance(preset_params, dict):
            worker_job = {
                "job_id": job_id,
                "job_path": str(self.storage.job_path(job_id)),
                "source_type": job.get("source_type", "twitch_vod"),
                "source_id": source_id,
                "transcription_id": transcription_id,
                "audio_path": str(audio_path),
                "transcript_dir": str(transcript_dir),
                "model": options.get("model") or self.model,
                "model_family": options.get("model_family") or "whisper",
                "device": preset_params.get("device") or self.device,
                "compute_type": preset_params.get("compute_type") or self.compute_type,
                "language": options.get("language") or self.language,
                "hotwords": options.get("hotwords"),
                "gpu_lock_dir": str(self.gpu_lock.data_dir),
                "preset_params": preset_params,
            }
        else:
            worker_job = {
                "job_id": job_id,
                "job_path": str(self.storage.job_path(job_id)),
                "source_type": job.get("source_type", "twitch_vod"),
                "source_id": source_id,
                "transcription_id": transcription_id,
                "audio_path": str(audio_path),
                "transcript_dir": str(transcript_dir),
                "model": options.get("model") or self.model,
                "model_family": options.get("model_family") or "whisper",
                "device": self.device,
                "compute_type": self.compute_type,
                "language": options.get("language") or self.language,
                "hotwords": options.get("hotwords"),
                "gpu_lock_dir": str(self.gpu_lock.data_dir),
            }
        worker_job_path = self.storage._job_dir(job_id) / "worker_job.json"  # noqa: SLF001
        with open(worker_job_path, "w", encoding="utf-8") as fh:
            json.dump(worker_job, fh, indent=2, ensure_ascii=False)

        log_path = self.storage.worker_log_path(job_id)
        try:
            log_fh = open(log_path, "wb", buffering=0)
        except OSError as exc:
            self._mark_failed(job_id, f"Could not open worker log file: {exc}")
            return
        cmd = [sys.executable, "-m", "ttvturbo.media_processing.transcription_worker", str(worker_job_path)]
        try:
            proc = subprocess.Popen(
                cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            try:
                log_fh.close()
            except OSError:
                pass
            self._mark_failed(job_id, f"Could not start worker subprocess: {exc}")
            return
        with self._lock:
            self._active[job_id] = proc
            self._active_log_fh[job_id] = log_fh
        # Mark RUNNING (the worker itself will refine the phase).
        job["status"] = MediaJobStatus.RUNNING.value
        job["started_at"] = _now_iso()
        job["progress"] = {"percent": None, "processed_seconds": None, "total_seconds": None, "phase": "WAITING_FOR_GPU"}
        job["updated_at"] = _now_iso()
        self.storage.save_job(job)
        reaper = threading.Thread(
            target=self._reap_worker, args=(job_id, proc, log_fh),
            daemon=True, name=f"transcription-reaper-{job_id}",
        )
        reaper.start()

    def _reap_worker(self, job_id: str, proc: subprocess.Popen, log_fh: Any) -> None:
        try:
            exit_code = proc.wait()
        except Exception:  # pragma: no cover
            exit_code = -1
        try:
            log_fh.close()
        except OSError:
            pass
        try:
            job = self.storage.load_job(job_id)
        except MediaJobStorageError:
            job = None
        if job is not None:
            status = job.get("status")
            if status in {s.value for s in TRANSIENT_JOB_STATUSES}:
                self._mark_failed(
                    job_id,
                    f"Transcription worker exited with code {exit_code} before completion.",
                )
        with self._lock:
            if self._active.get(job_id) is proc:
                self._active.pop(job_id, None)
                self._active_log_fh.pop(job_id, None)
        # Try to start any waiting jobs now that a slot freed up.
        self._start_waiting_jobs()

    def _start_waiting_jobs(self) -> None:
        """After a worker exits, try to start WAITING_FOR_GPU jobs."""
        for job in self.storage.iter_jobs():
            if job.get("job_type") != JobType.TRANSCRIBE.value:
                continue
            if job.get("status") == MediaJobStatus.WAITING_FOR_GPU.value:
                self._try_start_worker(job["id"])

    # ------------------------------------------------------------------ cancel/retry/delete
    def cancel_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        status = job.get("status")
        if status not in {s.value for s in CANCELLABLE_JOB_STATUSES}:
            raise MediaJobConflictError(
                f"Job can only be canceled while active (current: {status})."
            )
        with self._lock:
            proc = self._active.get(job_id)
        if proc is not None and proc.poll() is None:
            self._terminate(proc)
            try:
                proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._kill(proc)
            with self._lock:
                self._active.pop(job_id, None)
            self._close_log(job_id)
        job = self.storage.load_job(job_id)
        job["status"] = MediaJobStatus.CANCELED.value
        job["error"] = "Transcription was canceled by the user."
        job["completed_at"] = _now_iso()
        job["updated_at"] = _now_iso()
        self.storage.save_job(job)
        # Clean up .part transcript files.
        tid = job.get("transcription_id")
        sid = job.get("source_id")
        stype = job.get("source_type", "twitch_vod")
        if tid and sid:
            tdir = self.transcript_dir(sid, tid, stype)
            for name in (TRANSCRIPT_JSON, TRANSCRIPT_TXT, TRANSCRIPT_SRT, TRANSCRIPT_VTT):
                part = tdir / (name + TRANSCRIPT_PART_SUFFIX)
                try:
                    if part.exists():
                        part.unlink()
                except OSError:
                    pass
        # Release the GPU lock if the worker held it (it normally releases
        # on its own, but be safe).
        try:
            self.gpu_lock.release("transcription", job_id)
        except Exception:  # pragma: no cover
            pass
        # Start waiting jobs.
        self._start_waiting_jobs()
        return job

    def retry_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        status = job.get("status")
        if status not in {s.value for s in RETRYABLE_JOB_STATUSES}:
            raise MediaJobConflictError("Retry is only allowed for FAILED or CANCELED jobs.")
        # Reset and try to start.
        now = _now_iso()
        job["status"] = MediaJobStatus.WAITING_FOR_GPU.value
        job["error"] = None
        job["result"] = None
        job["progress"] = {"percent": None, "processed_seconds": None, "total_seconds": None, "phase": None}
        job["started_at"] = None
        job["completed_at"] = None
        job["updated_at"] = now
        self.storage.save_job(job)
        self._try_start_worker(job_id)
        return self.storage.load_job(job_id)

    def delete_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        status = job.get("status")
        if status in {s.value for s in TRANSIENT_JOB_STATUSES}:
            raise MediaJobConflictError("Cannot delete a running job. Cancel it first.")
        # Also delete the transcript artifact dir.
        tid = job.get("transcription_id")
        sid = job.get("source_id")
        stype = job.get("source_type", "twitch_vod")
        deleted = self.storage.delete_job(job_id)
        if tid and sid:
            tdir = self.transcript_dir(sid, tid, stype)
            try:
                if tdir.exists():
                    shutil.rmtree(tdir, ignore_errors=True)
            except OSError:
                pass
        return deleted

    def delete_transcription(self, transcription_id: str) -> bool:
        """Delete a transcript artifact by id. Does NOT delete the source
        video or the audio artifact.

        Also deletes any job record pointing to this transcription. If the
        transcript artifact dir does not exist (e.g. the job failed before
        producing one), the job is still cleaned up.
        """
        # Find and delete any job that points to this transcription.
        found_job = False
        for job in self.storage.iter_jobs():
            if job.get("transcription_id") == transcription_id:
                if job.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES}:
                    raise MediaJobConflictError(
                        "Cannot delete a transcript with a running job. Cancel the job first."
                    )
                try:
                    self.storage.delete_job(job["id"])
                    found_job = True
                except MediaJobStorageError:
                    pass
        # Best-effort: remove the transcript artifact dir if it exists.
        try:
            tdir = self._find_transcription_dir(transcription_id)
        except MediaJobNotFoundError:
            tdir = None
        if tdir is not None:
            try:
                shutil.rmtree(tdir, ignore_errors=True)
            except OSError:
                pass
        if not found_job and tdir is None:
            raise MediaJobNotFoundError(f"transcription not found: {transcription_id}")
        return True

    # ------------------------------------------------------------------ file access
    def transcript_file_path(self, transcription_id: str, ext: str) -> Path:
        """Return the path to a READY transcript file. Validates ext and
        path-traversal. Raises MediaJobNotFoundError if the transcript or
        the file does not exist.
        """
        if ext not in {"json", "txt", "srt", "vtt"}:
            raise MediaJobValidationError(f"unsupported transcript ext {ext!r}")
        rec = self.get_transcription(transcription_id)
        if rec.get("status") != TranscriptionStatus.READY.value:
            raise MediaJobConflictError(
                f"transcript is not READY (current: {rec.get('status')})"
            )
        vod_id = rec.get("source_id")
        if not vod_id:
            raise MediaJobNotFoundError("transcript record is missing source_id")
        tdir = self.transcript_dir(vod_id, transcription_id, rec.get("source_type"))
        filename = f"transcript.{ext}"
        path = (tdir / filename).resolve()
        try:
            path.relative_to(tdir.resolve())
        except ValueError as exc:
            raise MediaJobValidationError("path traversal") from exc
        if not path.is_file():
            raise MediaJobNotFoundError(f"transcript file not found: {filename}")
        return path

    # ------------------------------------------------------------------ misc
    def get_job(self, job_id: str) -> dict:
        try:
            return self.storage.load_job(job_id)
        except MediaJobStorageError as exc:
            raise MediaJobNotFoundError(str(exc)) from exc

    def list_jobs(self, source_type: Optional[str] = None, source_id: Optional[str] = None) -> list[dict]:
        jobs = [j for j in self.storage.iter_jobs() if j.get("job_type") == JobType.TRANSCRIBE.value]
        if source_type:
            jobs = [j for j in jobs if j.get("source_type") == source_type]
        if source_id:
            jobs = [j for j in jobs if j.get("source_id") == source_id]
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return jobs

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass

    # ------------------------------------------------------------------ shutdown
    def shutdown(self) -> None:
        """Terminate all active transcription workers and close log handles.

        Idempotent: safe to call multiple times.  Does not raise.
        """
        from ttvturbo.lifecycle import terminate_subprocess

        with self._lock:
            items = list(self._active.items())
        for job_id, proc in items:
            terminate_subprocess(proc, label=f"transcription-worker-{job_id}")
        with self._lock:
            for job_id in list(self._active.keys()):
                self._active.pop(job_id, None)
            for job_id in list(self._active_log_fh.keys()):
                self._close_log(job_id)

    def _close_log(self, job_id: str) -> None:
        fh = self._active_log_fh.pop(job_id, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def _mark_failed(self, job_id: str, reason: str) -> None:
        try:
            job = self.storage.load_job(job_id)
        except MediaJobStorageError:
            return
        job["status"] = MediaJobStatus.FAILED.value
        job["error"] = reason
        job["completed_at"] = _now_iso()
        job["updated_at"] = _now_iso()
        try:
            self.storage.save_job(job)
        except OSError:  # pragma: no cover
            pass

    def _recover_on_startup(self) -> None:
        """Mark transient TRANSCRIBE jobs as FAILED (subprocess is gone)."""
        for job in self.storage.iter_jobs():
            if job.get("job_type") != JobType.TRANSCRIBE.value:
                continue
            if job.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES}:
                job["status"] = MediaJobStatus.FAILED.value
                job["error"] = "Transcription was interrupted by a server restart."
                job["completed_at"] = _now_iso()
                job["updated_at"] = _now_iso()
                try:
                    self.storage.save_job(job)
                except OSError:  # pragma: no cover
                    pass
        # Reap a stale GPU lock left by a crashed transcription worker.
        try:
            self.gpu_lock._reap_stale()  # noqa: SLF001
        except Exception:  # pragma: no cover
            pass

    def aggregate_status(self) -> dict:
        jobs = [j for j in self.storage.iter_jobs() if j.get("job_type") == JobType.TRANSCRIBE.value]
        ready = sum(1 for j in jobs if j.get("status") == MediaJobStatus.READY.value)
        failed = sum(1 for j in jobs if j.get("status") == MediaJobStatus.FAILED.value)
        active = sum(1 for j in jobs if j.get("status") in {s.value for s in TRANSIENT_JOB_STATUSES})
        return {"total": len(jobs), "ready": ready, "failed": failed, "active": active}
