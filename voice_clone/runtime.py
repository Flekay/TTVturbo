"""Qwen3-TTS runtime + subprocess entry point for voice-clone generation.

Adapted from ``spikes/qwen_tts`` (REAL_MODEL VERIFIED). The spike remains
untouched; this is the production copy. The runtime class mirrors the
verified spike's measurement/teardown logic. The module is also runnable
as a subprocess so the FastAPI app stays responsive and CUDA memory is
released reliably when the worker exits.

Subprocess contract:

    python -m voice_clone.runtime <job.json>

``job.json`` contains:

    {
      "id": "...",
      "reference_audio": "/abs/path/ref.wav",
      "reference_text": "...",
      "target_text": "...",
      "language": "German",
      "output_path": "/abs/path/output.wav",
      "metadata_path": "/abs/path/metadata.json",
      "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
      "device": "cuda:0",
      "dtype": "bfloat16"
    }

The subprocess writes status updates to ``metadata_path`` at each phase and
exits 0 on READY, non-zero on FAILED. It NEVER produces a fake WAV.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import soundfile as sf

from .schemas import (
    DTYPE_DEFAULT,
    DEVICE_DEFAULT,
    LANGUAGE_DEFAULT,
    MODEL_ID_DEFAULT,
    GenerationStatus,
)


# ---------------------------------------------------------------------------
# Output validation (adapted from spikes/qwen_tts/diagnostics.py)
# ---------------------------------------------------------------------------

MIN_OUTPUT_SECONDS = 0.5


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_output(output_path: str, ref_audio_path: str) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the produced WAV.

    Hard errors mean the output is not a real, non-trivial generation.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not os.path.isfile(output_path):
        errors.append(f"output file does not exist: {output_path}")
        return errors, warnings

    try:
        data, sr = sf.read(output_path, always_2d=True)
    except Exception as exc:
        errors.append(f"output WAV is not readable with soundfile: {exc}")
        return errors, warnings

    if data.size == 0:
        errors.append("output WAV has zero samples")
        return errors, warnings

    duration = float(data.shape[0]) / float(sr)
    if duration <= MIN_OUTPUT_SECONDS:
        errors.append(f"output too short: {duration:.3f}s <= {MIN_OUTPUT_SECONDS}s")

    mono = data[:, 0] if data.ndim > 1 else data
    if not np.all(np.isfinite(mono)):
        errors.append("output contains NaN or infinity samples")

    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if not np.isfinite(peak):
        errors.append("output peak is outside the valid float range")
    elif peak == 0.0:
        errors.append("output is fully silent (all zeros)")

    try:
        if os.path.isfile(ref_audio_path) and file_sha256(output_path) == file_sha256(ref_audio_path):
            errors.append("output is byte-identical to the reference (no generation happened)")
    except OSError:
        pass

    return errors, warnings


# ---------------------------------------------------------------------------
# Runtime (adapted from spikes/qwen_tts/runtime.py)
# ---------------------------------------------------------------------------

_DTYPE_MAP: dict[str, Any] = {}


def _resolve_dtype(name: str) -> Any:
    """Resolve a dtype name to a torch dtype, lazily importing torch."""
    import torch

    if not _DTYPE_MAP:
        _DTYPE_MAP.update(
            {
                "bfloat16": torch.bfloat16,
                "bf16": torch.bfloat16,
                "float16": torch.float16,
                "fp16": torch.float16,
                "float32": torch.float32,
                "fp32": torch.float32,
            }
        )
    try:
        return _DTYPE_MAP[name.lower()]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unsupported dtype: {name}") from exc


@dataclass
class VramSnapshot:
    label: str
    allocated_bytes: int
    reserved_bytes: int
    free_bytes: int
    total_bytes: int

    @classmethod
    def capture(cls, label: str, device_index: int = 0) -> "VramSnapshot":
        import torch

        if not torch.cuda.is_available():
            return cls(label, 0, 0, 0, 0)
        torch.cuda.synchronize(device_index)
        props = torch.cuda.get_device_properties(device_index)
        total = props.total_memory
        allocated = torch.cuda.memory_allocated(device_index)
        reserved = torch.cuda.memory_reserved(device_index)
        free = max(0, total - reserved)
        return cls(label, allocated, reserved, free, total)


def _peak_ram() -> int:
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except Exception:  # pragma: no cover - psutil optional in odd envs
        return 0


def probe_attention_backend() -> tuple[str, str]:
    """Return (chosen_backend, fallback_note).

    Tries flash_attention_2 first; if the package is missing or the kernel
    cannot run on the current GPU, falls back to sdpa. A flash-attn failure
    must never produce a fake result.
    """
    try:
        import importlib

        importlib.import_module("flash_attn")  # type: ignore
        return "flash_attention_2", ""
    except Exception as exc:
        return "sdpa", f"flash_attention_2 unavailable ({type(exc).__name__}: {exc}); using sdpa"


class QwenTTSRuntime:
    """Owns the Qwen3TTSModel instance and surrounding measurement state."""

    def __init__(
        self,
        model_id: str = MODEL_ID_DEFAULT,
        device: str = DEVICE_DEFAULT,
        dtype: str = DTYPE_DEFAULT,
        attention_backend: Optional[str] = None,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.dtype_name = dtype
        self.dtype = _resolve_dtype(dtype)
        self.attention_backend = attention_backend
        self.attention_backend_fallback = ""
        self.model_revision = "unknown"
        self.model: Any = None
        self._device_index = 0
        if device.startswith("cuda:"):
            try:
                self._device_index = int(device.split(":", 1)[1])
            except ValueError:
                self._device_index = 0

        # metrics
        self.model_load_seconds = 0.0
        self.prompt_creation_seconds = 0.0
        self.generation_seconds = 0.0
        self.peak_vram_bytes = 0
        self.peak_ram_bytes = 0
        self.sample_rate = 0
        self.output_duration_seconds = 0.0
        self.vram_before_load: Optional[VramSnapshot] = None
        self.vram_peak_load: Optional[VramSnapshot] = None
        self.vram_peak_generation: Optional[VramSnapshot] = None
        self.vram_after_release: Optional[VramSnapshot] = None

    def _sync(self) -> None:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize(self._device_index)

    def _capture_peak(self, snapshot: Optional[VramSnapshot]) -> None:
        if snapshot is not None:
            self.peak_vram_bytes = max(self.peak_vram_bytes, snapshot.allocated_bytes)
        self.peak_ram_bytes = max(self.peak_ram_bytes, _peak_ram())

    def load(self) -> None:
        from qwen_tts import Qwen3TTSModel

        if self.attention_backend is None:
            backend, note = probe_attention_backend()
            self.attention_backend = backend
            self.attention_backend_fallback = note

        self.vram_before_load = VramSnapshot.capture("before_load", self._device_index)
        self._sync()
        t0 = time.time()
        self.model = Qwen3TTSModel.from_pretrained(
            self.model_id,
            device_map=self.device,
            dtype=self.dtype,
            attn_implementation=self.attention_backend,
        )
        self._sync()
        self.model_load_seconds = time.time() - t0
        self.vram_peak_load = VramSnapshot.capture("peak_load", self._device_index)
        self._capture_peak(self.vram_peak_load)
        self.model_revision = self._read_revision()

    def _read_revision(self) -> str:
        try:
            inner = getattr(self.model, "model", None)
            cfg = getattr(inner, "config", None) or getattr(self.model, "config", None)
            rev = getattr(cfg, "_commit_hash", None) or getattr(cfg, "revision", None)
            if rev:
                return str(rev)
        except Exception:
            pass
        return "unknown"

    def create_prompt(self, ref_audio: str, ref_text: str) -> Any:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        self._sync()
        t0 = time.time()
        prompt = self.model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=ref_text,
            x_vector_only_mode=False,
        )
        self._sync()
        self.prompt_creation_seconds = time.time() - t0
        return prompt

    def generate(self, text: str, language: str, voice_clone_prompt: Any) -> tuple[list, int]:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        self._sync()
        t0 = time.time()
        wavs, sr = self.model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=voice_clone_prompt,
        )
        self._sync()
        self.generation_seconds = time.time() - t0
        self.vram_peak_generation = VramSnapshot.capture("peak_generation", self._device_index)
        self._capture_peak(self.vram_peak_generation)
        return list(wavs), int(sr)

    def release(self) -> None:
        self.model = None
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize(self._device_index)
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        self.vram_after_release = VramSnapshot.capture("after_release", self._device_index)


# ---------------------------------------------------------------------------
# Subprocess entry point
# ---------------------------------------------------------------------------

def _atomic_write_json(path: str, payload: dict) -> None:
    """Write JSON atomically so readers never see a partial file."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _read_ref_duration(ref_audio: str) -> float:
    data, sr = sf.read(ref_audio)
    return float(len(data)) / float(sr)


def _save_wav_part(wav: np.ndarray, sr: int, part_path: str) -> None:
    """Write the WAV to a `.part` path so the final `output.wav` only ever
    appears once it has been validated and atomically renamed.

    The ``.part`` suffix does not let soundfile infer the container format,
    so we pass ``format="WAV"`` explicitly.
    """
    parent = os.path.dirname(os.path.abspath(part_path)) or "."
    os.makedirs(parent, exist_ok=True)
    sf.write(part_path, np.asarray(wav), sr, format="WAV")


def _finalize_output(part_path: str, output_path: str) -> dict[str, Any]:
    """Validate the `.part` file in place, compute metrics, then atomically
    rename it to the final `output.wav`. Returns the metric fields to merge
    into the metadata. Raises on any validation failure (the `.part` file is
    removed in that case so no invalid output remains).
    """
    if not os.path.isfile(part_path):
        raise RuntimeError(f"part file missing after generation: {part_path}")

    try:
        data, sr = sf.read(part_path, always_2d=True)
    except Exception as exc:
        try:
            os.unlink(part_path)
        except OSError:
            pass
        raise RuntimeError(f"output WAV is not readable with soundfile: {exc}") from exc

    if data.size == 0:
        os.unlink(part_path)
        raise RuntimeError("output WAV has zero samples")

    duration = float(data.shape[0]) / float(sr)
    if duration <= MIN_OUTPUT_SECONDS:
        os.unlink(part_path)
        raise RuntimeError(
            f"output too short: {duration:.3f}s <= {MIN_OUTPUT_SECONDS}s"
        )

    mono = data[:, 0] if data.ndim > 1 else data
    if not np.all(np.isfinite(mono)):
        os.unlink(part_path)
        raise RuntimeError("output contains NaN or infinity samples")

    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if not np.isfinite(peak):
        os.unlink(part_path)
        raise RuntimeError("output peak is outside the valid float range")
    if peak == 0.0:
        os.unlink(part_path)
        raise RuntimeError("output is fully silent (all zeros)")

    sha = file_sha256(part_path)
    file_size = os.path.getsize(part_path)
    # Atomic rename. On Windows, os.replace overwrites an existing target.
    os.replace(part_path, output_path)
    return {
        "output_sample_rate": int(sr),
        "output_duration_seconds": round(duration, 4),
        "output_file_size_bytes": int(file_size),
        "output_sha256": sha,
    }


def _run_job(job: dict) -> int:
    """Execute one generation job. Returns process exit code."""
    metadata_path = job["metadata_path"]
    base_payload: dict[str, Any] = {
        "id": job["id"],
        "reference_recording": job.get("reference_recording_name", ""),
        "reference_sha256": job.get("reference_sha256", ""),
        "reference_text": job["reference_text"],
        "target_text": job["target_text"],
        "language": job.get("language", LANGUAGE_DEFAULT),
        "model_id": job.get("model_id", MODEL_ID_DEFAULT),
        "model_revision": "unknown",
        "created_at": job["created_at"],
        "completed_at": None,
        "output_duration_seconds": None,
        "output_sample_rate": None,
        "output_file_size_bytes": None,
        "output_sha256": None,
        "generation_seconds": None,
        "peak_vram_bytes": None,
        "attention_backend": None,
        "worker_exit_code": None,
        "quality": job.get("quality", {}),
        "failure_reason": None,
        "warnings": job.get("warnings", []),
    }

    def write_status(status: GenerationStatus, **updates: Any) -> None:
        payload = dict(base_payload)
        payload.update(updates)
        payload["status"] = status.value
        _atomic_write_json(metadata_path, payload)

    ref_audio = job["reference_audio"]
    output_path = job["output_path"]
    part_path = output_path + ".part"
    # Never inherit a stale .part file from a previous crashed run.
    try:
        os.unlink(part_path)
    except OSError:
        pass

    runtime = QwenTTSRuntime(
        model_id=job.get("model_id", MODEL_ID_DEFAULT),
        device=job.get("device", DEVICE_DEFAULT),
        dtype=job.get("dtype", DTYPE_DEFAULT),
    )

    exit_code = 1
    try:
        write_status(GenerationStatus.LOADING_MODEL)
        runtime.load()
        base_payload["model_revision"] = runtime.model_revision
        base_payload["attention_backend"] = runtime.attention_backend

        write_status(GenerationStatus.GENERATING)
        prompt = runtime.create_prompt(ref_audio=ref_audio, ref_text=job["reference_text"])
        wavs, sr = runtime.generate(
            text=job["target_text"],
            language=job.get("language", LANGUAGE_DEFAULT),
            voice_clone_prompt=prompt,
        )
        if not wavs:
            raise RuntimeError("model returned no audio")
        wav = wavs[0]
        runtime.sample_rate = int(sr)
        runtime.output_duration_seconds = float(len(wav)) / float(sr)

        # 1. Write to .part
        _save_wav_part(wav, sr, part_path)

        # 2. Validate + atomic rename
        write_status(GenerationStatus.VALIDATING_OUTPUT)
        metrics = _finalize_output(part_path, output_path)
        base_payload.update(metrics)

        completed_at = _now_iso()
        base_payload["completed_at"] = completed_at
        base_payload["generation_seconds"] = round(runtime.generation_seconds, 4)
        base_payload["peak_vram_bytes"] = int(runtime.peak_vram_bytes)
        base_payload["model_revision"] = runtime.model_revision

        # 3. Cross-check against the reference (byte-identity, etc.).
        errors, warnings = validate_output(output_path, ref_audio)
        for w in warnings:
            base_payload["warnings"].append(w)

        if errors:
            try:
                os.unlink(output_path)
            except OSError:
                pass
            write_status(
                GenerationStatus.FAILED,
                completed_at=completed_at,
                failure_reason="; ".join(errors),
            )
            exit_code = 1
            return exit_code

        write_status(GenerationStatus.READY, completed_at=completed_at)
        exit_code = 0
        return exit_code
    except Exception as exc:  # noqa: BLE001 - surface any model failure honestly
        # Remove any partial or final output so a FAILED job has no WAV.
        for p in (part_path, output_path):
            try:
                os.unlink(p)
            except OSError:
                pass
        write_status(
            GenerationStatus.FAILED,
            completed_at=_now_iso(),
            failure_reason=f"{type(exc).__name__}: {exc}",
        )
        exit_code = 1
        return exit_code
    finally:
        # Always record the worker exit code so the service can diagnose
        # a crash even if the metadata write above raced.
        try:
            current = base_payload
            current["worker_exit_code"] = exit_code
            # Best-effort final metadata refresh so worker_exit_code is
            # persisted even when the try-block returned early.
            if os.path.isfile(metadata_path):
                with open(metadata_path, "r", encoding="utf-8") as fh:
                    persisted = json.load(fh)
                persisted["worker_exit_code"] = exit_code
                _atomic_write_json(metadata_path, persisted)
        except Exception:  # pragma: no cover - defensive
            pass
        runtime.release()


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m voice_clone.runtime <job.json>", file=sys.stderr)
        return 2
    job_path = argv[1]
    with open(job_path, "r", encoding="utf-8") as fh:
        job = json.load(fh)
    return _run_job(job)


if __name__ == "__main__":  # pragma: no cover - subprocess entry
    sys.exit(main(sys.argv[1:]))
