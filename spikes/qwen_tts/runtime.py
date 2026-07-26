"""Runtime helpers for the Qwen3-TTS voice-clone spike.

This module isolates everything that touches the large model: attention
backend probing, model loading, VRAM/RAM measurement, prompt creation,
generation and a clean teardown routine.

It must NEVER silently substitute a fake result for a real one. If model
loading or generation fails the caller receives the original exception.
"""

from __future__ import annotations

import gc
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import torch


MODEL_ID_DEFAULT = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
DEVICE_DEFAULT = "cuda:0"
DTYPE_DEFAULT = "bfloat16"
LANGUAGE_DEFAULT = "German"

_DTYPE_MAP = {
    "bfloat16": torch.bfloat16,
    "bf16": torch.bfloat16,
    "float16": torch.float16,
    "fp16": torch.float16,
    "float32": torch.float32,
    "fp32": torch.float32,
}


def resolve_dtype(name: str) -> torch.dtype:
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
        if not torch.cuda.is_available():
            return cls(label, 0, 0, 0, 0)
        torch.cuda.synchronize(device_index)
        props = torch.cuda.get_device_properties(device_index)
        total = props.total_memory
        allocated = torch.cuda.memory_allocated(device_index)
        reserved = torch.cuda.memory_reserved(device_index)
        free = max(0, total - reserved)
        return cls(label, allocated, reserved, free, total)


@dataclass
class RuntimeMetrics:
    model_id: str
    model_revision: str = "unknown"
    device: str = DEVICE_DEFAULT
    dtype: str = DTYPE_DEFAULT
    attention_backend: str = "unknown"
    attention_backend_fallback: str = ""
    reference_duration_seconds: float = 0.0
    output_duration_seconds: float = 0.0
    model_load_seconds: float = 0.0
    prompt_creation_seconds: float = 0.0
    generation_seconds: float = 0.0
    peak_vram_bytes: int = 0
    peak_ram_bytes: int = 0
    sample_rate: int = 0
    output_sha256: str = ""
    vram_before_load: Optional[VramSnapshot] = None
    vram_peak_load: Optional[VramSnapshot] = None
    vram_peak_generation: Optional[VramSnapshot] = None
    vram_after_release: Optional[VramSnapshot] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        def snap(s: Optional[VramSnapshot]) -> Optional[dict[str, Any]]:
            if s is None:
                return None
            return {
                "label": s.label,
                "allocated_bytes": s.allocated_bytes,
                "reserved_bytes": s.reserved_bytes,
                "free_bytes": s.free_bytes,
                "total_bytes": s.total_bytes,
            }

        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "device": self.device,
            "dtype": self.dtype,
            "attention_backend": self.attention_backend,
            "attention_backend_fallback": self.attention_backend_fallback,
            "reference_duration_seconds": round(self.reference_duration_seconds, 4),
            "output_duration_seconds": round(self.output_duration_seconds, 4),
            "model_load_seconds": round(self.model_load_seconds, 4),
            "prompt_creation_seconds": round(self.prompt_creation_seconds, 4),
            "generation_seconds": round(self.generation_seconds, 4),
            "peak_vram_bytes": int(self.peak_vram_bytes),
            "peak_ram_bytes": int(self.peak_ram_bytes),
            "sample_rate": int(self.sample_rate),
            "output_sha256": self.output_sha256,
            "vram_before_load": snap(self.vram_before_load),
            "vram_peak_load": snap(self.vram_peak_load),
            "vram_peak_generation": snap(self.vram_peak_generation),
            "vram_after_release": snap(self.vram_after_release),
            "extra": dict(self.extra),
        }


def _peak_vram(metrics: RuntimeMetrics) -> int:
    candidates = [
        metrics.vram_peak_load,
        metrics.vram_peak_generation,
        metrics.vram_before_load,
        metrics.vram_after_release,
    ]
    return max((s.allocated_bytes for s in candidates if s is not None), default=0)


def _peak_ram() -> int:
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except Exception:  # pragma: no cover - psutil optional in odd envs
        import resource

        return getattr(resource, "getrusage", lambda *a: type("R", (), {"ru_maxrss": 0}))(
            resource.RUSAGE_SELF
        ).ru_maxrss * 1024


def probe_attention_backend() -> tuple[str, str]:
    """Return (chosen_backend, fallback_note).

    Tries flash_attention_2 first; if the package is missing or the kernel
    cannot run on the current GPU, falls back to sdpa. A flash-attn failure
    must never produce a fake result, so we only change the backend string,
    not the model output.
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
        self.dtype = resolve_dtype(dtype)
        self.metrics = RuntimeMetrics(
            model_id=model_id,
            device=device,
            dtype=dtype,
        )
        if attention_backend:
            self.metrics.attention_backend = attention_backend
            self.metrics.attention_backend_fallback = "explicitly forced by caller"
        else:
            backend, note = probe_attention_backend()
            self.metrics.attention_backend = backend
            self.metrics.attention_backend_fallback = note
        self.model: Any = None
        self._device_index = 0
        if device.startswith("cuda:"):
            try:
                self._device_index = int(device.split(":", 1)[1])
            except ValueError:
                self._device_index = 0

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        from qwen_tts import Qwen3TTSModel  # imported lazily so tests can mock

        self.metrics.vram_before_load = VramSnapshot.capture(
            "before_load", self._device_index
        )
        torch.cuda.synchronize(self._device_index) if torch.cuda.is_available() else None
        t0 = time.time()
        self.model = Qwen3TTSModel.from_pretrained(
            self.model_id,
            device_map=self.device,
            dtype=self.dtype,
            attn_implementation=self.metrics.attention_backend,
        )
        torch.cuda.synchronize(self._device_index) if torch.cuda.is_available() else None
        self.metrics.model_load_seconds = time.time() - t0
        self.metrics.vram_peak_load = VramSnapshot.capture(
            "peak_load", self._device_index
        )
        self.metrics.peak_vram_bytes = max(
            self.metrics.peak_vram_bytes, self.metrics.vram_peak_load.allocated_bytes
        )
        self.metrics.peak_ram_bytes = max(self.metrics.peak_ram_bytes, _peak_ram())
        self.metrics.model_revision = self._read_revision()

    def _read_revision(self) -> str:
        """Best-effort read of the model revision from the loaded config."""
        try:
            inner = getattr(self.model, "model", None)
            cfg = getattr(inner, "config", None) or getattr(self.model, "config", None)
            rev = getattr(cfg, "_commit_hash", None) or getattr(cfg, "revision", None)
            if rev:
                return str(rev)
        except Exception:
            pass
        return "unknown"

    # ------------------------------------------------------------- prompt/gen
    def create_prompt(
        self,
        ref_audio: str,
        ref_text: str,
        x_vector_only_mode: bool = False,
    ) -> Any:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        torch.cuda.synchronize(self._device_index) if torch.cuda.is_available() else None
        t0 = time.time()
        prompt_items = self.model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=ref_text,
            x_vector_only_mode=x_vector_only_mode,
        )
        torch.cuda.synchronize(self._device_index) if torch.cuda.is_available() else None
        self.metrics.prompt_creation_seconds = time.time() - t0
        return prompt_items

    def generate(
        self,
        text: str,
        language: str,
        voice_clone_prompt: Any,
        **gen_kwargs: Any,
    ) -> tuple[list, int]:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        torch.cuda.synchronize(self._device_index) if torch.cuda.is_available() else None
        t0 = time.time()
        wavs, sr = self.model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=voice_clone_prompt,
            **gen_kwargs,
        )
        torch.cuda.synchronize(self._device_index) if torch.cuda.is_available() else None
        self.metrics.generation_seconds = time.time() - t0
        self.metrics.vram_peak_generation = VramSnapshot.capture(
            "peak_generation", self._device_index
        )
        self.metrics.peak_vram_bytes = max(
            self.metrics.peak_vram_bytes,
            self.metrics.vram_peak_generation.allocated_bytes,
            _peak_vram(self.metrics),
        )
        self.metrics.peak_ram_bytes = max(self.metrics.peak_ram_bytes, _peak_ram())
        return list(wavs), int(sr)

    # ------------------------------------------------------------- teardown
    def release(self) -> None:
        self.model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.synchronize(self._device_index)
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        self.metrics.vram_after_release = VramSnapshot.capture(
            "after_release", self._device_index
        )

    # ----------------------------------------------------------------- util
    def finalize_metrics(self) -> None:
        self.metrics.peak_vram_bytes = _peak_vram(self.metrics)
        self.metrics.peak_ram_bytes = max(self.metrics.peak_ram_bytes, _peak_ram())
