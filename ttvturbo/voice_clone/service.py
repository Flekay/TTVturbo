"""Voice-clone service: validation, persistence, subprocess orchestration.

This module is the single place that knows how to:

* validate a generation request (path safety, WAV readability, text limits,
  reference quality analysis);
* persist generation metadata atomically under ``voice_clones/{id}/``;
* spawn the Qwen3-TTS worker subprocess and keep the FastAPI app responsive;
* enforce at most one concurrent TTS generation;
* recover persisted state on server restart;
* report real GPU/runtime availability via :mod:`voice_clone.diagnostics`.

No model code is imported here. The heavy stack lives only inside the
subprocess (``voice_clone.runtime``), so unit tests run without torch.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from .diagnostics import diagnose_runtime
from .quality import AnalysisError, Quality, analyze_reference
from .schemas import (
    LANGUAGE_DEFAULT,
    MAX_TARGET_CHARS,
    MIN_REF_SECONDS,
    MAX_REF_SECONDS,
    RECOMMENDED_REF_MIN,
    RECOMMENDED_REF_MAX,
    MODEL_ID_DEFAULT,
    DEVICE_DEFAULT,
    DTYPE_DEFAULT,
    GenerationMetadata,
    GenerationStatus,
)

from ttvturbo.storage_utils import atomic_write_json as _central_atomic_write_json

logger = logging.getLogger("ttvturbo.voice_clone")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 300.0
TIMEOUT_ENV = "TTVTURBO_VOICE_CLONE_TIMEOUT_SECONDS"

# Hard kill grace period after a graceful terminate.
KILL_GRACE_SECONDS = 5.0

# Transient states: if the worker exits while in one of these, the job is
# automatically marked FAILED by the reaper.
TRANSIENT_STATUSES = frozenset({
    GenerationStatus.QUEUED,
    GenerationStatus.VALIDATING_REFERENCE,
    GenerationStatus.LOADING_MODEL,
    GenerationStatus.GENERATING,
    GenerationStatus.VALIDATING_OUTPUT,
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Hard validation failure that prevents generation from starting."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class VoiceCloneService:
    """Filesystem-backed service with a single concurrency slot."""

    def __init__(
        self,
        recordings_dir: Path,
        voice_clones_dir: Path,
        model_id: str = MODEL_ID_DEFAULT,
        device: str = DEVICE_DEFAULT,
        dtype: str = DTYPE_DEFAULT,
        timeout_seconds: Optional[float] = None,
        gpu_lock: Any = None,
    ) -> None:
        self.recordings_dir = recordings_dir
        self.voice_clones_dir = voice_clones_dir
        self.voice_clones_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.device = device
        self.dtype = dtype
        # Optional project-wide GPU lock shared with faster-whisper
        # transcription. When set, the service acquires the lock before
        # spawning the worker and releases it when the worker exits, so
        # voice-clone and transcription never load their models at the
        # same time on the single 12 GB GPU. The lock is a
        # media_processing.gpu_lock.GpuLock instance (duck-typed).
        self._gpu_lock = gpu_lock
        # Optional profile-mode resolver injected by the app. When set, the
        # voice-clone service can resolve a reference from a voice profile's
        # accepted reference. Signature:
        #   (profile_id, script_id) -> {
        #       "recording_filename": str,
        #       "script_text": str,
        #       "profile_name": str,
        #   }
        # It raises ValidationError on unknown profile/script or non-ACCEPTED
        # references. Keeping this as a delegate avoids a circular import
        # between voice_clone and voice_profiles.
        self._profile_reference_resolver = None

        if timeout_seconds is None:
            env_val = os.environ.get(TIMEOUT_ENV)
            if env_val:
                try:
                    timeout_seconds = float(env_val)
                except ValueError:
                    timeout_seconds = DEFAULT_TIMEOUT_SECONDS
            else:
                timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self.timeout_seconds = float(timeout_seconds)

        self._lock = threading.Lock()
        self._active_id: Optional[str] = None
        self._active_proc: Optional[subprocess.Popen] = None
        self._active_log_fh: Optional[Any] = None
        self._active_log_path: Optional[Path] = None
        self._diagnostics_cache: Optional[dict] = None
        self._diagnostics_ts: float = 0.0

        # Recover persisted state. Any job still in a transient state after a
        # restart is marked FAILED: its subprocess is gone.
        self._recover_on_startup()

    # ------------------------------------------------------------------ profile resolver
    def set_profile_reference_resolver(self, resolver) -> None:
        """Inject the voice-profile reference resolver for profile mode."""
        self._profile_reference_resolver = resolver

    # ------------------------------------------------------------------ paths
    def _generation_dir(self, generation_id: str) -> Path:
        return self.voice_clones_dir / generation_id

    def _metadata_path(self, generation_id: str) -> Path:
        return self._generation_dir(generation_id) / "metadata.json"

    def _output_path(self, generation_id: str) -> Path:
        return self._generation_dir(generation_id) / "output.wav"

    def _part_path(self, generation_id: str) -> Path:
        return self._generation_dir(generation_id) / "output.wav.part"

    def _worker_log_path(self, generation_id: str) -> Path:
        return self._generation_dir(generation_id) / "worker.log"

    def _safe_generation_id(self, generation_id: str) -> str:
        """Reject anything that is not a plain hex uuid."""
        try:
            uuid.UUID(generation_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError("Invalid generation id.") from exc
        return generation_id

    def _resolve_reference(self, reference_recording: str) -> Path:
        """Resolve a reference filename to an absolute path inside recordings_dir.

        Blocks path traversal, absolute paths, hidden/temp files, non-WAV.
        """
        if not reference_recording or not isinstance(reference_recording, str):
            raise ValidationError("reference_recording is empty.")
        if "/" in reference_recording or "\\" in reference_recording:
            raise ValidationError("reference_recording must be a plain filename.")
        if reference_recording.startswith(".") or reference_recording.startswith("~"):
            raise ValidationError("reference_recording must not be a hidden file.")
        safe = Path(reference_recording).name
        if safe != reference_recording:
            raise ValidationError("reference_recording must be a plain filename.")
        if not safe.lower().endswith(".wav"):
            raise ValidationError("reference_recording must be a .wav file.")
        resolved = (self.recordings_dir / safe).resolve()
        try:
            resolved.relative_to(self.recordings_dir.resolve())
        except ValueError as exc:
            raise ValidationError("reference_recording escapes the recordings directory.") from exc
        if not resolved.is_file():
            raise ValidationError(f"reference_recording does not exist: {safe}")
        return resolved

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _now_iso() -> str:
        return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        """Atomically write generation metadata.

        Delegates to the central :func:`storage_utils.atomic_write_json`
        so the temp file uses the reserved ``.{name}.{pid}.{ns}.tmp``
        pattern (avoiding the fixed-name ``metadata.json.tmp`` collision
        that two concurrent writers could hit) and gains Windows-lock
        retry behaviour.
        """
        _central_atomic_write_json(path, payload, ValidationError, kind="voice_clone")

    def _write_metadata(self, generation_id: str, payload: dict) -> None:
        self._atomic_write_json(self._metadata_path(generation_id), payload)

    def _read_metadata(self, generation_id: str) -> Optional[dict]:
        path = self._metadata_path(generation_id)
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read metadata %s: %s", path, exc)
            return None

    # ------------------------------------------------------------------ startup
    def _recover_on_startup(self) -> None:
        """Mark any transient-state job as FAILED after a restart.

        Also removes leftover ``.part`` files. Faulty or incomplete metadata
        must never prevent the server from starting: bad entries are skipped
        with a warning.
        """
        try:
            entries = list(self.voice_clones_dir.iterdir())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not scan voice_clones dir on startup: %s", exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            meta_path = entry / "metadata.json"
            if not meta_path.is_file():
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Skipping unreadable metadata %s: %s", meta_path, exc)
                continue
            status_str = payload.get("status")
            try:
                status = GenerationStatus(status_str)
            except ValueError:
                logger.warning("Skipping metadata with unknown status %s", status_str)
                continue
            if status in TRANSIENT_STATUSES:
                payload["status"] = GenerationStatus.FAILED.value
                payload["failure_reason"] = (
                    payload.get("failure_reason")
                    or "Server was restarted while the generation was in progress."
                )
                if not payload.get("completed_at"):
                    payload["completed_at"] = self._now_iso()
                # Remove any partial output so a FAILED job has no
                # seemingly-valid WAV.
                for name in ("output.wav", "output.wav.part"):
                    p = entry / name
                    if p.is_file():
                        try:
                            p.unlink()
                        except OSError:
                            pass
                try:
                    self._atomic_write_json(meta_path, payload)
                except OSError as exc:  # pragma: no cover - defensive
                    logger.warning("Could not persist recovery for %s: %s", meta_path, exc)
            elif status == GenerationStatus.READY:
                # A READY job must have a real, valid output.wav. If the file
                # is missing or a stale .part exists, clean the .part but do
                # NOT touch the READY record (the WAV may simply have been
                # moved away by the user).
                part = entry / "output.wav.part"
                if part.is_file():
                    try:
                        part.unlink()
                    except OSError:
                        pass

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        """Return the voice-clone module status.

        Merges the orchestration slot state with the real GPU/runtime
        diagnostics from :mod:`voice_clone.diagnostics`. The diagnostics
        are cached briefly (10 s) so polling the status endpoint does not
        re-import torch on every request.
        """
        with self._lock:
            active_id = self._active_id
            # If the process died without clearing the slot, clear it now.
            if active_id and self._active_proc is not None:
                if self._active_proc.poll() is not None:
                    self._active_id = None
                    self._active_proc = None
                    active_id = None
        diag = self._diagnostics()
        return {
            "available": diag["available"],
            "busy": active_id is not None,
            "active_generation_id": active_id,
            "model_id": self.model_id,
            # Additive diagnostic fields (frontend ignores unknown keys).
            "device": diag["device"],
            "python_version": diag["python_version"],
            "torch_version": diag["torch_version"],
            "torch_cuda_version": diag["torch_cuda_version"],
            "cuda_available": diag["cuda_available"],
            "device_name": diag["device_name"],
            "vram_total_bytes": diag["vram_total_bytes"],
            "vram_free_bytes": diag["vram_free_bytes"],
            "qwen_tts_importable": diag["qwen_tts_importable"],
            "model_cached": diag.get("model_cached", False),
            "soundfile_ok": diag["soundfile_ok"],
            "ffmpeg_ok": diag["ffmpeg_ok"],
            "data_dir_writable": diag["data_dir_writable"],
            "reasons": diag["reasons"],
            "warnings": diag["warnings"],
        }

    def preload_model(self) -> dict:
        """Pre-download the configured Qwen3-TTS model from HuggingFace Hub
        into the local cache so the first generation does not block on a
        multi-GB download.

        Returns a dict with ``ok``, ``model_id`` and optional ``error``
        keys. This method is synchronous and may take a while; the API
        endpoint runs it in a thread so the FastAPI event loop stays
        responsive.
        """
        try:
            from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
        except Exception as exc:
            return {
                "ok": False,
                "model_id": self.model_id,
                "error": f"huggingface_hub not importable: {type(exc).__name__}: {exc}",
            }
        try:
            snapshot_download(repo_id=self.model_id, local_files_only=False)
        except Exception as exc:
            return {
                "ok": False,
                "model_id": self.model_id,
                "error": f"download failed: {type(exc).__name__}: {exc}",
            }
        # Invalidate the diagnostics cache so the next status probe reflects
        # the newly cached model.
        self._diagnostics_cache = None
        return {
            "ok": True,
            "model_id": self.model_id,
        }

    def _diagnostics(self) -> dict:
        """Cached runtime diagnostics. Cache TTL is 10 seconds."""
        import time as _time

        now = _time.monotonic()
        if (
            self._diagnostics_cache is not None
            and (now - self._diagnostics_ts) < 10.0
        ):
            return self._diagnostics_cache
        report = diagnose_runtime(
            model_id=self.model_id,
            device=self.device,
            data_dir=str(self.voice_clones_dir),
        )
        self._diagnostics_cache = report
        self._diagnostics_ts = now
        return report

    def invalidate_diagnostics(self) -> None:
        """Force the next status() call to re-run the diagnostics."""
        self._diagnostics_cache = None
        self._diagnostics_ts = 0.0

    # ------------------------------------------------------------------ analyze
    def analyze_reference(self, reference_recording: str) -> dict:
        """Run the technical quality analysis on a recording and return the
        full analysis result dict. Validates path safety first.
        """
        ref_path = self._resolve_reference(reference_recording)
        result = analyze_reference(str(ref_path))
        return result.to_dict()

    # ------------------------------------------------------------------ list
    def list_generations(self) -> list[dict]:
        items: list[dict] = []
        for entry in self.voice_clones_dir.iterdir():
            if not entry.is_dir():
                continue
            meta = self._read_metadata(entry.name)
            if meta is None:
                continue
            items.append(meta)
        items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return items

    def get_generation(self, generation_id: str) -> Optional[dict]:
        gid = self._safe_generation_id(generation_id)
        return self._read_metadata(gid)

    def output_path_for(self, generation_id: str) -> Optional[Path]:
        """Return the output.wav path if the generation is READY, else None."""
        gid = self._safe_generation_id(generation_id)
        meta = self._read_metadata(gid)
        if meta is None:
            return None
        if meta.get("status") != GenerationStatus.READY.value:
            return None
        out = self._output_path(gid)
        if not out.is_file():
            return None
        return out

    # ------------------------------------------------------------------ delete
    def delete_generation(self, generation_id: str) -> bool:
        gid = self._safe_generation_id(generation_id)
        with self._lock:
            if self._active_id == gid:
                raise ValidationError("Cannot delete a generation that is currently running.")
        gen_dir = self._generation_dir(gid)
        if not gen_dir.is_dir():
            return False
        # Remove the directory tree atomically-ish: rename then delete.
        import shutil

        tmp = gen_dir.with_name(gen_dir.name + ".deleting")
        try:
            os.replace(gen_dir, tmp)
        except OSError as exc:
            raise ValidationError(f"Could not delete generation: {exc}") from exc
        shutil.rmtree(tmp, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ create
    def create_generation(self, request: dict) -> dict:
        """Validate, persist a QUEUED record, and start the worker subprocess.

        Returns the initial metadata dict (with id + status).

        Two mutually exclusive modes are supported:

        * **manual** (legacy): ``reference_recording`` + ``reference_text``
          are supplied by the client.
        * **profile**: ``voice_profile_id`` + ``voice_profile_script_id``
          are supplied; the server resolves the accepted reference's WAV
          filename and script text. The client cannot override either.
        """
        reference_recording = request.get("reference_recording", "")
        reference_text = request.get("reference_text", "")
        target_text = request.get("target_text", "")
        language = request.get("language", LANGUAGE_DEFAULT)
        allow_quality_warning = bool(request.get("allow_quality_warning", False))
        voice_profile_id = request.get("voice_profile_id")
        voice_profile_script_id = request.get("voice_profile_script_id")

        # Mode detection: the two modes are mutually exclusive. Mixing or
        # omitting both is a hard validation error.
        has_manual = bool(reference_recording) or bool(reference_text)
        has_profile = bool(voice_profile_id) or bool(voice_profile_script_id)
        if has_manual and has_profile:
            raise ValidationError(
                "Provide either manual reference fields or voice-profile fields, not both."
            )
        if not has_manual and not has_profile:
            raise ValidationError(
                "Either manual reference fields or voice-profile fields are required."
            )

        # Profile-mode metadata; populated only in profile mode.
        profile_meta: dict[str, Any] = {}

        if has_profile:
            if not voice_profile_id or not voice_profile_script_id:
                raise ValidationError(
                    "voice_profile_id and voice_profile_script_id are both required in profile mode."
                )
            if self._profile_reference_resolver is None:
                raise ValidationError(
                    "Voice-profile mode is not available on this server."
                )
            try:
                resolved = self._profile_reference_resolver(
                    voice_profile_id, voice_profile_script_id
                )
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(
                    f"Could not resolve voice-profile reference: {exc}"
                ) from exc
            reference_recording = resolved["recording_filename"]
            reference_text = resolved["script_text"]
            profile_meta = {
                "voice_profile_id": voice_profile_id,
                "voice_profile_name": resolved.get("profile_name"),
                "voice_profile_script_id": voice_profile_script_id,
            }

        # 1. Text validation (no file access needed).
        if not reference_text or not reference_text.strip():
            raise ValidationError("reference_text is empty.")
        if not target_text or not target_text.strip():
            raise ValidationError("target_text is empty.")
        if len(target_text) > MAX_TARGET_CHARS:
            raise ValidationError(
                f"target_text too long ({len(target_text)} > {MAX_TARGET_CHARS} chars)."
            )

        # 2. Reference path safety.
        ref_path = self._resolve_reference(reference_recording)

        # 3. Concurrency slot.
        with self._lock:
            if self._active_id is not None:
                # Reap if the process already exited.
                if self._active_proc is not None and self._active_proc.poll() is not None:
                    self._active_id = None
                    self._active_proc = None
                else:
                    raise ValidationError(
                        "A voice-clone generation is already running. Wait for it to finish."
                    )
            generation_id = uuid.uuid4().hex
            gen_dir = self._generation_dir(generation_id)
            gen_dir.mkdir(parents=True, exist_ok=True)
            # Reserve the slot only after we know we will start the subprocess.
            self._active_id = generation_id

        def _abort_before_start(reason: str) -> None:
            """Release the slot and remove the empty generation directory so
            no incomplete entry or empty UUID folder accumulates.
            """
            self._release_slot(generation_id)
            try:
                import shutil as _shutil

                if gen_dir.is_dir():
                    _shutil.rmtree(gen_dir, ignore_errors=True)
            except Exception:  # pragma: no cover - defensive
                pass

        # 4. Reference quality analysis (REJECT aborts; REVIEW warns).
        quality_payload: dict[str, Any] = {}
        warnings: list[str] = []
        try:
            result = analyze_reference(str(ref_path))
            quality_payload = result.to_dict()
            vcr = result.voice_clone_reference
            if vcr.quality == Quality.REJECT:
                failure_reason = "Reference quality REJECT: " + "; ".join(vcr.reasons)
                _abort_before_start(failure_reason)
                raise ValidationError(failure_reason)
            if vcr.quality == Quality.REVIEW:
                warnings.extend(vcr.warnings)
                if not allow_quality_warning:
                    failure_reason = (
                        "Reference quality is REVIEW. Review the warnings and confirm to proceed: "
                        + "; ".join(vcr.warnings)
                    )
                    _abort_before_start(failure_reason)
                    raise ValidationError(failure_reason)
        except AnalysisError as exc:
            _abort_before_start(str(exc))
            raise ValidationError(f"Reference analysis failed: {exc}") from exc

        # 5. Persist QUEUED metadata.
        reference_sha = self._file_sha256(ref_path)
        meta = self._initial_metadata(
            generation_id,
            reference_recording,
            reference_text,
            target_text,
            language,
            quality_payload,
            warnings,
        )
        meta["reference_sha256"] = reference_sha
        meta["status"] = GenerationStatus.QUEUED.value
        # Additive profile-mode metadata. Older generations never had these
        # fields; readers must tolerate their absence.
        if profile_meta:
            meta.update(profile_meta)
        self._write_metadata(generation_id, meta)

        # 6. Build the job file and start the subprocess.
        output_path = self._output_path(generation_id)
        metadata_path = self._metadata_path(generation_id)
        job = {
            "id": generation_id,
            "reference_audio": str(ref_path),
            "reference_recording_name": reference_recording,
            "reference_sha256": reference_sha,
            "reference_text": reference_text,
            "target_text": target_text,
            "language": language,
            "output_path": str(output_path),
            "metadata_path": str(metadata_path),
            "model_id": self.model_id,
            "device": self.device,
            "dtype": self.dtype,
            "created_at": meta["created_at"],
            "quality": quality_payload,
            "warnings": warnings,
        }
        job_path = gen_dir / "job.json"
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh, indent=2, ensure_ascii=False)

        self._start_worker(generation_id, job_path)
        return meta

    def _initial_metadata(
        self,
        generation_id: str,
        reference_recording: str,
        reference_text: str,
        target_text: str,
        language: str,
        quality_payload: dict,
        warnings: list[str],
    ) -> dict:
        return {
            "id": generation_id,
            "status": GenerationStatus.QUEUED.value,
            "reference_recording": reference_recording,
            "reference_sha256": "",
            "reference_text": reference_text,
            "target_text": target_text,
            "language": language,
            "model_id": self.model_id,
            "model_revision": "unknown",
            "created_at": self._now_iso(),
            "completed_at": None,
            "output_duration_seconds": None,
            "output_sample_rate": None,
            "output_file_size_bytes": None,
            "output_sha256": None,
            "generation_seconds": None,
            "peak_vram_bytes": None,
            "attention_backend": None,
            "worker_exit_code": None,
            "quality": quality_payload,
            "failure_reason": None,
            "warnings": list(warnings),
        }

    def _release_slot(self, generation_id: str) -> None:
        with self._lock:
            if self._active_id == generation_id:
                self._active_id = None
                self._active_proc = None
                # Close the worker log file handle if it is the active one.
                if self._active_log_fh is not None:
                    try:
                        self._active_log_fh.close()
                    except OSError:
                        pass
                    self._active_log_fh = None
                self._active_log_path = None
        # Release the project-wide GPU lock if we still own it. Done
        # outside the service lock to avoid holding it during disk IO.
        if self._gpu_lock is not None:
            try:
                self._gpu_lock.release("voice_clone", generation_id)
            except Exception:  # pragma: no cover - defensive
                pass

    def _start_worker(self, generation_id: str, job_path: Path) -> None:
        """Spawn the Qwen3-TTS subprocess. Non-blocking.

        stdout AND stderr are redirected to a real log file
        ``voice_clones/{id}/worker.log``. We never use ``stderr=PIPE``
        because the reaper thread does not continuously drain the pipe,
        and a worker that emits a lot of output would deadlock.

        If a project-wide GPU lock is configured, it is acquired here
        (before spawning) and released in :meth:`_release_slot` when the
        worker exits. The lock owner type is ``"voice_clone"``.
        """
        # Acquire the project-wide GPU lock before spawning the worker.
        # The worker itself does NOT acquire the lock — the parent process
        # owns it for the lifetime of the generation so the lock is
        # released reliably even if the worker crashes.
        if self._gpu_lock is not None:
            try:
                self._gpu_lock.acquire("voice_clone", generation_id)
            except Exception as exc:
                # Map a busy lock to a validation error so the API returns
                # a clean 400/409 instead of a 500.
                meta = self._read_metadata(generation_id) or {}
                meta["status"] = GenerationStatus.FAILED.value
                meta["failure_reason"] = (
                    f"GPU is busy. Wait for the current job to finish. ({exc})"
                )
                meta["completed_at"] = self._now_iso()
                self._write_metadata(generation_id, meta)
                self._release_slot(generation_id)
                raise ValidationError(str(exc)) from exc
        cmd = [sys.executable, "-m", "ttvturbo.voice_clone.runtime", str(job_path)]
        log_path = self._worker_log_path(generation_id)
        try:
            log_fh = open(log_path, "wb", buffering=0)
        except OSError as exc:
            self._release_slot(generation_id)
            meta = self._read_metadata(generation_id) or {}
            meta["status"] = GenerationStatus.FAILED.value
            meta["failure_reason"] = f"Could not open worker log file: {exc}"
            meta["completed_at"] = self._now_iso()
            self._write_metadata(generation_id, meta)
            raise ValidationError(f"Could not open worker log file: {exc}") from exc

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            try:
                log_fh.close()
            except OSError:
                pass
            self._release_slot(generation_id)
            meta = self._read_metadata(generation_id) or {}
            meta["status"] = GenerationStatus.FAILED.value
            meta["failure_reason"] = f"Could not start worker subprocess: {exc}"
            meta["completed_at"] = self._now_iso()
            self._write_metadata(generation_id, meta)
            raise ValidationError(f"Could not start worker subprocess: {exc}") from exc

        with self._lock:
            self._active_id = generation_id
            self._active_proc = proc
            self._active_log_fh = log_fh
            self._active_log_path = log_path

        # Reap the process in a background thread so the slot is released
        # automatically when the worker exits or the timeout fires. This
        # thread does NOT touch the model; it only waits for the subprocess.
        reaper = threading.Thread(
            target=self._reap_worker,
            args=(generation_id, proc, log_fh, log_path),
            daemon=True,
            name=f"voice-clone-reaper-{generation_id}",
        )
        reaper.start()

    def _reap_worker(
        self,
        generation_id: str,
        proc: subprocess.Popen,
        log_fh: Any,
        log_path: Path,
    ) -> None:
        """Wait for the worker, enforce the timeout, and finalize the status.

        On exit:
        1. capture the exit code;
        2. reload the metadata;
        3. if the status is still transient, atomically set FAILED with the
           exit code (or a timeout message);
        4. if the status is READY, verify the output is actually present and
           valid - otherwise downgrade to FAILED;
        5. release the GPU slot and close the log file.
        """
        timed_out = False
        try:
            exit_code = proc.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_worker(proc, generation_id)
            try:
                exit_code = proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._kill_worker(proc, generation_id)
                try:
                    exit_code = proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:  # pragma: no cover - very unusual
                    exit_code = -1
        except Exception:  # pragma: no cover - defensive
            exit_code = -1

        # Close the log file handle now that the process is gone.
        try:
            log_fh.close()
        except OSError:
            pass

        self._finalize_after_exit(generation_id, exit_code, timed_out)

        with self._lock:
            if self._active_id == generation_id:
                self._active_id = None
                self._active_proc = None
                if self._active_log_fh is log_fh:
                    self._active_log_fh = None
                self._active_log_path = None

    def _terminate_worker(self, proc: subprocess.Popen, generation_id: str) -> None:
        """Graceful termination first."""
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):  # pragma: no cover - defensive
            pass

    def _kill_worker(self, proc: subprocess.Popen, generation_id: str) -> None:
        """Hard kill as a last resort."""
        try:
            proc.kill()
        except (OSError, ProcessLookupError):  # pragma: no cover - defensive
            pass

    def _finalize_after_exit(
        self, generation_id: str, exit_code: int, timed_out: bool
    ) -> None:
        """Reload metadata and atomically finalize the status if needed.

        * A transient status after exit -> FAILED with the exit code (or a
          timeout message).
        * A READY status with a missing/invalid output.wav -> FAILED.
        * A READY status with a stale .part file -> .part removed.
        * Otherwise the persisted status is preserved.
        """
        meta = self._read_metadata(generation_id)
        if meta is None:
            # The worker never managed to write any metadata. Record a
            # minimal FAILED entry so the user sees a concrete reason.
            meta = {
                "id": generation_id,
                "status": GenerationStatus.FAILED.value,
                "failure_reason": (
                    "Voice clone worker timed out and was terminated."
                    if timed_out
                    else f"Voice clone worker exited with code {exit_code} "
                    "without writing metadata."
                ),
                "completed_at": self._now_iso(),
                "worker_exit_code": exit_code,
            }
            try:
                self._write_metadata(generation_id, meta)
            except OSError:  # pragma: no cover - defensive
                pass
            self._cleanup_part(generation_id)
            return

        meta["worker_exit_code"] = exit_code
        status_str = meta.get("status")
        try:
            status = GenerationStatus(status_str)
        except ValueError:
            # Unknown / corrupt status -> mark FAILED.
            meta["status"] = GenerationStatus.FAILED.value
            meta["failure_reason"] = (
                meta.get("failure_reason")
                or f"Voice clone worker exited with code {exit_code} "
                "and left an unknown status."
            )
            if not meta.get("completed_at"):
                meta["completed_at"] = self._now_iso()
            self._cleanup_part_and_output(generation_id)
            try:
                self._write_metadata(generation_id, meta)
            except OSError:  # pragma: no cover - defensive
                pass
            return

        if status in TRANSIENT_STATUSES:
            meta["status"] = GenerationStatus.FAILED.value
            if timed_out:
                meta["failure_reason"] = (
                    meta.get("failure_reason")
                    or f"Voice clone worker timed out after {self.timeout_seconds:.0f}s "
                    f"and was terminated (exit code {exit_code})."
                )
            else:
                meta["failure_reason"] = (
                    meta.get("failure_reason")
                    or f"Voice clone worker exited with code {exit_code} "
                    "while the generation was still in progress."
                )
            if not meta.get("completed_at"):
                meta["completed_at"] = self._now_iso()
            self._cleanup_part_and_output(generation_id)
            try:
                self._write_metadata(generation_id, meta)
            except OSError:  # pragma: no cover - defensive
                pass
            return

        if status == GenerationStatus.READY:
            # Verify the output is actually present and plausible.
            out = self._output_path(generation_id)
            part = self._part_path(generation_id)
            if part.is_file():
                try:
                    part.unlink()
                except OSError:
                    pass
            if not out.is_file():
                meta["status"] = GenerationStatus.FAILED.value
                meta["failure_reason"] = (
                    meta.get("failure_reason")
                    or "Voice clone worker reported READY but output.wav is missing."
                )
                if not meta.get("completed_at"):
                    meta["completed_at"] = self._now_iso()
                try:
                    self._write_metadata(generation_id, meta)
                except OSError:  # pragma: no cover - defensive
                    pass
                return
            # If the metadata claims READY but essential fields are missing,
            # downgrade to FAILED.
            required = (
                meta.get("output_sha256"),
                meta.get("output_sample_rate"),
                meta.get("output_duration_seconds"),
                meta.get("output_file_size_bytes"),
            )
            if any(v is None for v in required):
                meta["status"] = GenerationStatus.FAILED.value
                meta["failure_reason"] = (
                    meta.get("failure_reason")
                    or "Voice clone worker reported READY but output metadata is incomplete."
                )
                if not meta.get("completed_at"):
                    meta["completed_at"] = self._now_iso()
                try:
                    self._write_metadata(generation_id, meta)
                except OSError:  # pragma: no cover - defensive
                    pass
                return

        # Status is FAILED (or READY with valid output): persist the exit
        # code we just learned.
        try:
            self._write_metadata(generation_id, meta)
        except OSError:  # pragma: no cover - defensive
            pass

    def _cleanup_part(self, generation_id: str) -> None:
        part = self._part_path(generation_id)
        if part.is_file():
            try:
                part.unlink()
            except OSError:
                pass

    def _cleanup_part_and_output(self, generation_id: str) -> None:
        for p in (self._part_path(generation_id), self._output_path(generation_id)):
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------ worker log
    def worker_log_excerpt(self, generation_id: str, max_bytes: int = 4096) -> Optional[str]:
        """Return a short, sanitized tail of the worker log.

        Returns None if the generation id is invalid or no log exists.
        Full local paths are scrubbed so the API does not leak filesystem
        layout. The log never contains reference audio data (the worker
        only ever receives file paths, not audio buffers, on its stdout).
        """
        try:
            self._safe_generation_id(generation_id)
        except ValidationError:
            return None
        log_path = self._worker_log_path(generation_id)
        if not log_path.is_file():
            return None
        try:
            size = log_path.stat().st_size
            with open(log_path, "rb") as fh:
                if size > max_bytes:
                    fh.seek(-max_bytes, os.SEEK_END)
                raw = fh.read()
        except OSError:
            return None
        text = raw.decode("utf-8", errors="replace")
        # Scrub absolute paths (Windows drive letters + POSIX). Best-effort.
        # Use a replacement function so trailing backslashes in the
        # replacement are not interpreted as regex escapes.
        import re

        def _win_repl(m: "re.Match[str]") -> str:
            return "<path>\\"

        def _posix_repl(m: "re.Match[str]") -> str:
            return "/"

        text = re.sub(r"[A-Za-z]:\\[^\s\"']+\\", _win_repl, text)
        text = re.sub(r"/(?:[^\s\"'/]+/)+", _posix_repl, text)
        return text[-max_bytes:]
