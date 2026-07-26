"""Voice-clone service: validation, persistence, subprocess orchestration.

This module is the single place that knows how to:

* validate a generation request (path safety, WAV readability, text limits,
  reference quality analysis);
* persist generation metadata atomically under ``voice_clones/{id}/``;
* spawn the Qwen3-TTS worker subprocess and keep the FastAPI app responsive;
* enforce at most one concurrent TTS generation;
* recover persisted state on server restart.

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

logger = logging.getLogger("ttvturbo.voice_clone")


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
    ) -> None:
        self.recordings_dir = recordings_dir
        self.voice_clones_dir = voice_clones_dir
        self.voice_clones_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.device = device
        self.dtype = dtype

        self._lock = threading.Lock()
        self._active_id: Optional[str] = None
        self._active_proc: Optional[subprocess.Popen] = None

        # Recover persisted state. Any job still in a transient state after a
        # restart is marked FAILED: its subprocess is gone.
        self._recover_on_startup()

    # ------------------------------------------------------------------ paths
    def _generation_dir(self, generation_id: str) -> Path:
        return self.voice_clones_dir / generation_id

    def _metadata_path(self, generation_id: str) -> Path:
        return self._generation_dir(generation_id) / "metadata.json"

    def _output_path(self, generation_id: str) -> Path:
        return self._generation_dir(generation_id) / "output.wav"

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
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

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
        """Mark any transient-state job as FAILED after a restart."""
        for entry in self.voice_clones_dir.iterdir():
            if not entry.is_dir():
                continue
            meta_path = entry / "metadata.json"
            if not meta_path.is_file():
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            status_str = payload.get("status")
            try:
                status = GenerationStatus(status_str)
            except ValueError:
                continue
            if status in (GenerationStatus.QUEUED, GenerationStatus.VALIDATING_REFERENCE,
                          GenerationStatus.LOADING_MODEL, GenerationStatus.GENERATING,
                          GenerationStatus.VALIDATING_OUTPUT):
                payload["status"] = GenerationStatus.FAILED.value
                payload["failure_reason"] = (
                    payload.get("failure_reason")
                    or "Server was restarted while the generation was in progress."
                )
                if not payload.get("completed_at"):
                    payload["completed_at"] = self._now_iso()
                # Remove any partial output so a FAILED job has no seemingly-valid WAV.
                out = entry / "output.wav"
                if out.is_file():
                    try:
                        out.unlink()
                    except OSError:
                        pass
                self._atomic_write_json(meta_path, payload)

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        """Return the voice-clone module status."""
        with self._lock:
            active_id = self._active_id
            # If the process died without clearing the slot, clear it now.
            if active_id and self._active_proc is not None:
                if self._active_proc.poll() is not None:
                    self._active_id = None
                    self._active_proc = None
                    active_id = None
            return {
                "available": True,
                "busy": active_id is not None,
                "active_generation_id": active_id,
                "model_id": self.model_id,
            }

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
        """
        reference_recording = request.get("reference_recording", "")
        reference_text = request.get("reference_text", "")
        target_text = request.get("target_text", "")
        language = request.get("language", LANGUAGE_DEFAULT)
        allow_quality_warning = bool(request.get("allow_quality_warning", False))

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

        # 4. Reference quality analysis (REJECT aborts; REVIEW warns).
        quality_payload: dict[str, Any] = {}
        warnings: list[str] = []
        try:
            result = analyze_reference(str(ref_path))
            quality_payload = result.to_dict()
            vcr = result.voice_clone_reference
            if vcr.quality == Quality.REJECT:
                # Release the slot and write a FAILED record.
                self._release_slot(generation_id)
                failure_reason = "Reference quality REJECT: " + "; ".join(vcr.reasons)
                meta = self._initial_metadata(
                    generation_id,
                    reference_recording,
                    reference_text,
                    target_text,
                    language,
                    quality_payload,
                    warnings,
                )
                meta["status"] = GenerationStatus.FAILED.value
                meta["failure_reason"] = failure_reason
                meta["completed_at"] = self._now_iso()
                self._write_metadata(generation_id, meta)
                raise ValidationError(failure_reason)
            if vcr.quality == Quality.REVIEW:
                warnings.extend(vcr.warnings)
                if not allow_quality_warning:
                    self._release_slot(generation_id)
                    failure_reason = (
                        "Reference quality is REVIEW. Review the warnings and confirm to proceed: "
                        + "; ".join(vcr.warnings)
                    )
                    meta = self._initial_metadata(
                        generation_id,
                        reference_recording,
                        reference_text,
                        target_text,
                        language,
                        quality_payload,
                        warnings,
                    )
                    meta["status"] = GenerationStatus.FAILED.value
                    meta["failure_reason"] = failure_reason
                    meta["completed_at"] = self._now_iso()
                    self._write_metadata(generation_id, meta)
                    raise ValidationError(failure_reason)
        except AnalysisError as exc:
            self._release_slot(generation_id)
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
            "generation_seconds": None,
            "peak_vram_bytes": None,
            "quality": quality_payload,
            "failure_reason": None,
            "warnings": list(warnings),
        }

    def _release_slot(self, generation_id: str) -> None:
        with self._lock:
            if self._active_id == generation_id:
                self._active_id = None
                self._active_proc = None

    def _start_worker(self, generation_id: str, job_path: Path) -> None:
        """Spawn the Qwen3-TTS subprocess. Non-blocking."""
        cmd = [sys.executable, "-m", "voice_clone.runtime", str(job_path)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
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

        # Reap the process in a background thread so the slot is released
        # automatically when the worker exits. This thread does NOT touch
        # the model; it only waits for the subprocess to finish.
        reaper = threading.Thread(
            target=self._reap_worker,
            args=(generation_id, proc),
            daemon=True,
            name=f"voice-clone-reaper-{generation_id}",
        )
        reaper.start()

    def _reap_worker(self, generation_id: str, proc: subprocess.Popen) -> None:
        try:
            proc.wait()
        except Exception:  # pragma: no cover - defensive
            pass
        with self._lock:
            if self._active_id == generation_id:
                self._active_id = None
                self._active_proc = None
