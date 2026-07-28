"""ASR model adapters and benchmark candidates.

Three concrete adapters with a minimal common contract:

  * :class:`FasterWhisperAdapter` — wraps faster-whisper (CTranslate2).
  * :class:`ParakeetAdapter` — wraps NVIDIA NeMo Parakeet TDT 0.6B v3.
  * :class:`CanaryAdapter` — wraps NVIDIA NeMo Canary 1B v2.

Each adapter's ``transcribe()`` returns a :class:`NormalizedTranscriptionResult`
with backend-independent fields. Unsupported values are ``None`` — no
invented metrics.

Six benchmark candidates are defined (Section 6 of the spec):

  * ``whisper-legacy-current`` — exact production config.
  * ``whisper-large-v3-forced-de-no-vad`` — German forced, no VAD.
  * ``whisper-large-v3-forced-en-no-vad`` — English forced, no VAD.
  * ``parakeet-tdt-0.6b-v3-auto`` — Parakeet auto language detection.
  * ``canary-1b-v2-de`` — Canary German transcription.
  * ``canary-1b-v2-en`` — Canary English transcription.

NVIDIA NeMo dependencies are optional. The base application starts
without NeMo installed. Availability is checked lazily — no model
loading at server startup.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logger = logging.getLogger("ttvturbo.media_processing.asr_models")


# ---------------------------------------------------------------------------
# Normalized result
# ---------------------------------------------------------------------------


@dataclass
class NormalizedTranscriptionResult:
    """Backend-independent transcription result."""

    model_family: str
    model_id: str
    model_revision: Optional[str] = None
    language: Optional[str] = None
    language_probability: Optional[float] = None
    text: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    words: list[dict[str, Any]] = field(default_factory=list)
    load_seconds: float = 0.0
    inference_seconds: float = 0.0
    total_seconds: float = 0.0
    model_reused: bool = False
    peak_vram_bytes: Optional[int] = None
    peak_ram_bytes: Optional[int] = None
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Model candidate definitions
# ---------------------------------------------------------------------------


@dataclass
class ModelCandidate:
    """A benchmark candidate with a fixed configuration."""

    id: str
    model_family: str  # "whisper", "parakeet", "canary"
    model_id: str
    name: str
    description: str
    options: dict[str, Any] = field(default_factory=dict)
    production_eligible: bool = True
    diagnostic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# The six benchmark candidates from the spec.
CANDIDATES: list[ModelCandidate] = [
    ModelCandidate(
        id="whisper-legacy-current",
        model_family="whisper",
        model_id="large-v3",
        name="Whisper – bisherige Konfiguration",
        description="large-v3, int8_float16, language=de, VAD an, beam_size=1.",
        options={
            "model": "large-v3",
            "compute_type": "int8_float16",
            "language": "de",
            "multilingual": False,
            "vad_filter": True,
            "beam_size": 1,
            "condition_on_previous_text": True,
            "word_timestamps": True,
        },
        production_eligible=True,
    ),
    ModelCandidate(
        id="whisper-large-v3-forced-de-no-vad",
        model_family="whisper",
        model_id="large-v3",
        name="Whisper – Deutsch erzwungen, ohne VAD",
        description="large-v3, float16, language=de, VAD aus, condition_on_previous_text=false.",
        options={
            "model": "large-v3",
            "compute_type": "float16",
            "language": "de",
            "multilingual": False,
            "vad_filter": False,
            "beam_size": 5,
            "condition_on_previous_text": False,
            "word_timestamps": True,
        },
        production_eligible=True,
    ),
    ModelCandidate(
        id="whisper-large-v3-forced-en-no-vad",
        model_family="whisper",
        model_id="large-v3",
        name="Whisper – Englisch erzwungen, ohne VAD",
        description="large-v3, float16, language=en, VAD aus, condition_on_previous_text=false.",
        options={
            "model": "large-v3",
            "compute_type": "float16",
            "language": "en",
            "multilingual": False,
            "vad_filter": False,
            "beam_size": 5,
            "condition_on_previous_text": False,
            "word_timestamps": True,
        },
        production_eligible=True,
    ),
    ModelCandidate(
        id="parakeet-tdt-0.6b-v3-auto",
        model_family="parakeet",
        model_id="nvidia/parakeet-tdt-0.6b-v3",
        name="NVIDIA Parakeet TDT 0.6B v3 – Auto",
        description="Parakeet TDT 0.6B v3 mit automatischer Spracherkennung.",
        options={
            "language": None,  # auto-detect
        },
        production_eligible=True,
    ),
    ModelCandidate(
        id="canary-1b-v2-de",
        model_family="canary",
        model_id="nvidia/canary-1b-v2",
        name="NVIDIA Canary 1B v2 – Deutsch",
        description="Canary 1B v2, source_lang=de, target_lang=de, Transcription.",
        options={
            "source_lang": "de",
            "target_lang": "de",
        },
        production_eligible=True,
    ),
    ModelCandidate(
        id="canary-1b-v2-en",
        model_family="canary",
        model_id="nvidia/canary-1b-v2",
        name="NVIDIA Canary 1B v2 – Englisch",
        description="Canary 1B v2, source_lang=en, target_lang=en, Transcription.",
        options={
            "source_lang": "en",
            "target_lang": "en",
        },
        production_eligible=True,
    ),
]

CANDIDATE_MAP: dict[str, ModelCandidate] = {c.id: c for c in CANDIDATES}


def list_model_candidates() -> list[dict[str, Any]]:
    """Return all benchmark candidates as dicts with availability info."""
    out = []
    for c in CANDIDATES:
        d = c.to_dict()
        d["available"] = check_candidate_available(c.id)
        out.append(d)
    return out


def get_candidate(candidate_id: str) -> Optional[ModelCandidate]:
    return CANDIDATE_MAP.get(candidate_id)


def check_candidate_available(candidate_id: str) -> bool:
    c = CANDIDATE_MAP.get(candidate_id)
    if c is None:
        return False
    if c.model_family == "whisper":
        return check_faster_whisper_available()
    if c.model_family == "parakeet":
        return check_parakeet_available()
    if c.model_family == "canary":
        return check_canary_available()
    return False


# ---------------------------------------------------------------------------
# Availability checks (lazy, no model loading)
# ---------------------------------------------------------------------------


def check_faster_whisper_available() -> bool:
    try:
        import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def check_parakeet_available() -> bool:
    try:
        import nemo.collections.asr as nemo_asr  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def check_canary_available() -> bool:
    try:
        import nemo.collections.asr as nemo_asr  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def check_nemo_installed() -> bool:
    try:
        import nemo  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# VRAM measurement via NVML (backend-independent)
# ---------------------------------------------------------------------------


class VramTracker:
    """Tracks VRAM usage via NVML (pynvml), independent of the ML backend.

    faster-whisper uses CTranslate2, not PyTorch, so torch.cuda.max_memory_allocated
    does not capture its VRAM. NVML reads the actual GPU device memory.

    If pynvml or NVML is not available, all values are ``None`` and a
    warning is emitted. We never report 0 bytes when a model is on CUDA.
    """

    def __init__(self, gpu_index: int = 0) -> None:
        self.gpu_index = gpu_index
        self._nvml = None
        self._handle = None
        self._available = False
        self.vram_before_bytes: Optional[int] = None
        self.vram_after_load_bytes: Optional[int] = None
        self.peak_vram_bytes: Optional[int] = None
        self.vram_after_release_bytes: Optional[int] = None
        self._monitoring = False
        self._warning: Optional[str] = None

    def init(self) -> None:
        try:
            import pynvml  # type: ignore[import-not-found]
            pynvml.nvmlInit()
            self._nvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self.gpu_index)
            self._available = True
        except Exception as exc:
            self._available = False
            self._warning = f"NVML unavailable: {type(exc).__name__}: {exc}"
            logger.warning(self._warning)

    def measure_before_load(self) -> None:
        if not self._available:
            return
        self.vram_before_bytes = self._read_vram()

    def measure_after_load(self) -> None:
        if not self._available:
            return
        self.vram_after_load_bytes = self._read_vram()
        self.peak_vram_bytes = self.vram_after_load_bytes

    def measure_after_release(self) -> None:
        if not self._available:
            return
        self.vram_after_release_bytes = self._read_vram()

    def _read_vram(self) -> Optional[int]:
        if not self._available or self._handle is None:
            return None
        try:
            info = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
            return int(info.used)
        except Exception as exc:
            logger.warning("NVML read failed: %s", exc)
            return None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def warning(self) -> Optional[str]:
        return self._warning

    def to_dict(self) -> dict[str, Optional[int]]:
        return {
            "vram_before_bytes": self.vram_before_bytes,
            "vram_after_load_bytes": self.vram_after_load_bytes,
            "peak_vram_bytes": self.peak_vram_bytes,
            "vram_after_release_bytes": self.vram_after_release_bytes,
        }

    def shutdown(self) -> None:
        if self._available and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# RAM measurement via psutil (already a project dependency)
# ---------------------------------------------------------------------------


def measure_peak_ram() -> Optional[int]:
    """Measure current process RSS via psutil. Returns bytes or None."""
    try:
        import psutil  # type: ignore[import-not-found]
        proc = psutil.Process()
        mem = proc.memory_info()
        return int(mem.rss)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class AsrAdapterError(Exception):
    """Raised when an adapter fails."""


class FasterWhisperAdapter:
    """Adapter for faster-whisper (CTranslate2 backend)."""

    def __init__(self) -> None:
        self._model = None
        self._loaded_model_id: Optional[str] = None
        self._loaded_compute_type: Optional[str] = None

    def transcribe(
        self, audio_path: str, options: dict[str, Any], vram_tracker: Optional[VramTracker] = None
    ) -> NormalizedTranscriptionResult:
        model_id = options.get("model", "large-v3")
        compute_type = options.get("compute_type", "int8_float16")
        device = options.get("device", "cuda")
        language = options.get("language") or None
        vad_filter = options.get("vad_filter", True)
        beam_size = options.get("beam_size", 5)
        condition_on_previous = options.get("condition_on_previous_text", True)
        word_timestamps = options.get("word_timestamps", True)

        warnings: list[str] = []
        model_reused = (
            self._model is not None
            and self._loaded_model_id == model_id
            and self._loaded_compute_type == compute_type
        )

        t0 = time.monotonic()
        if not model_reused:
            # Release old model.
            self._model = None
            self._loaded_model_id = None
            self._loaded_compute_type = None
            if vram_tracker:
                vram_tracker.measure_before_load()
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]
            except Exception as exc:
                raise AsrAdapterError(f"faster-whisper not installed: {exc}") from exc
            repo_id = (
                f"Systran/faster-whisper-{model_id}"
                if not model_id.startswith("/")
                else model_id
            )
            try:
                self._model = WhisperModel(
                    repo_id, device=device, compute_type=compute_type,
                )
            except Exception as exc:
                raise AsrAdapterError(f"failed to load model {repo_id}: {exc}") from exc
            self._loaded_model_id = model_id
            self._loaded_compute_type = compute_type
            if vram_tracker:
                vram_tracker.measure_after_load()
        load_seconds = time.monotonic() - t0 if not model_reused else 0.0

        # Transcribe.
        t1 = time.monotonic()
        try:
            segments_iter, info = self._model.transcribe(
                str(audio_path),
                language=language,
                task="transcribe",
                word_timestamps=word_timestamps,
                vad_filter=vad_filter,
                beam_size=beam_size,
                condition_on_previous_text=condition_on_previous,
            )
        except Exception as exc:
            raise AsrAdapterError(f"transcription failed: {exc}") from exc

        segments: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        full_text_parts: list[str] = []
        for seg in segments_iter:
            seg_words: list[dict[str, Any]] = []
            for w in (getattr(seg, "words", None) or []):
                wd = {
                    "start": float(getattr(w, "start", 0.0)),
                    "end": float(getattr(w, "end", 0.0)),
                    "text": str(getattr(w, "word", "")),
                    "probability": float(getattr(w, "probability", 0.0)),
                }
                seg_words.append(wd)
                words.append(wd)
            segments.append({
                "id": int(getattr(seg, "id", 0)),
                "start": float(getattr(seg, "start", 0.0)),
                "end": float(getattr(seg, "end", 0.0)),
                "text": str(getattr(seg, "text", "")).strip(),
                "no_speech_prob": float(getattr(seg, "no_speech_prob", 0.0)),
                "words": seg_words,
            })
            full_text_parts.append(str(getattr(seg, "text", "")).strip())
        inference_seconds = time.monotonic() - t1

        detected_lang = getattr(info, "language", None) if info else None
        lang_prob = getattr(info, "language_probability", None) if info else None

        peak_ram = measure_peak_ram()
        peak_vram = vram_tracker.peak_vram_bytes if vram_tracker else None

        return NormalizedTranscriptionResult(
            model_family="whisper",
            model_id=model_id,
            model_revision=None,
            language=detected_lang or language,
            language_probability=lang_prob,
            text=" ".join(full_text_parts).strip(),
            segments=segments,
            words=words,
            load_seconds=round(load_seconds, 3),
            inference_seconds=round(inference_seconds, 3),
            total_seconds=round(load_seconds + inference_seconds, 3),
            model_reused=model_reused,
            peak_vram_bytes=peak_vram,
            peak_ram_bytes=peak_ram,
            warnings=warnings,
        )

    def release(self) -> None:
        self._model = None
        self._loaded_model_id = None
        self._loaded_compute_type = None


class ParakeetAdapter:
    """Adapter for NVIDIA Parakeet TDT 0.6B v3 via NeMo."""

    def __init__(self) -> None:
        self._model = None
        self._loaded_model_id: Optional[str] = None

    def transcribe(
        self, audio_path: str, options: dict[str, Any], vram_tracker: Optional[VramTracker] = None
    ) -> NormalizedTranscriptionResult:
        model_id = options.get("model_id", "nvidia/parakeet-tdt-0.6b-v3")
        warnings: list[str] = []
        model_reused = self._model is not None and self._loaded_model_id == model_id

        t0 = time.monotonic()
        if not model_reused:
            self._model = None
            self._loaded_model_id = None
            if vram_tracker:
                vram_tracker.measure_before_load()
            try:
                from nemo.collections.asr.models import ASRModel  # type: ignore[import-not-found]
            except Exception as exc:
                raise AsrAdapterError(f"NeMo not installed: {exc}") from exc
            try:
                self._model = ASRModel.from_pretrained(model_id)
            except Exception as exc:
                raise AsrAdapterError(f"failed to load Parakeet {model_id}: {exc}") from exc
            self._loaded_model_id = model_id
            if vram_tracker:
                vram_tracker.measure_after_load()
        load_seconds = time.monotonic() - t0 if not model_reused else 0.0

        t1 = time.monotonic()
        try:
            result = self._model.transcribe([str(audio_path)])
        except Exception as exc:
            raise AsrAdapterError(f"Parakeet transcription failed: {exc}") from exc
        inference_seconds = time.monotonic() - t1

        # NeMo returns a list of results. Extract text and timestamps.
        text = ""
        segments: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        if isinstance(result, list) and len(result) > 0:
            entry = result[0]
            if isinstance(entry, dict):
                text = str(entry.get("text", "")).strip()
            elif isinstance(entry, str):
                text = entry.strip()
            # NeMo may provide timestamps in some models.
            if isinstance(entry, dict) and "timestamp" in entry:
                ts = entry["timestamp"]
                if isinstance(ts, list):
                    for t in ts:
                        if isinstance(t, (list, tuple)) and len(t) >= 2:
                            segments.append({
                                "start": float(t[0]),
                                "end": float(t[1]),
                                "text": str(t[2]) if len(t) > 2 else "",
                            })

        peak_ram = measure_peak_ram()
        peak_vram = vram_tracker.peak_vram_bytes if vram_tracker else None

        return NormalizedTranscriptionResult(
            model_family="parakeet",
            model_id=model_id,
            model_revision=None,
            language=None,  # Parakeet auto-detects; API may not expose the code
            language_probability=None,
            text=text,
            segments=segments,
            words=words,
            load_seconds=round(load_seconds, 3),
            inference_seconds=round(inference_seconds, 3),
            total_seconds=round(load_seconds + inference_seconds, 3),
            model_reused=model_reused,
            peak_vram_bytes=peak_vram,
            peak_ram_bytes=peak_ram,
            warnings=warnings,
        )

    def release(self) -> None:
        self._model = None
        self._loaded_model_id = None


class CanaryAdapter:
    """Adapter for NVIDIA Canary 1B v2 via NeMo."""

    def __init__(self) -> None:
        self._model = None
        self._loaded_model_id: Optional[str] = None

    def transcribe(
        self, audio_path: str, options: dict[str, Any], vram_tracker: Optional[VramTracker] = None
    ) -> NormalizedTranscriptionResult:
        model_id = options.get("model_id", "nvidia/canary-1b-v2")
        source_lang = options.get("source_lang", "de")
        target_lang = options.get("target_lang", "de")
        warnings: list[str] = []
        model_reused = self._model is not None and self._loaded_model_id == model_id

        t0 = time.monotonic()
        if not model_reused:
            self._model = None
            self._loaded_model_id = None
            if vram_tracker:
                vram_tracker.measure_before_load()
            try:
                from nemo.collections.asr.models import EncDecMultiTaskModel  # type: ignore[import-not-found]
            except Exception as exc:
                raise AsrAdapterError(f"NeMo not installed: {exc}") from exc
            try:
                self._model = EncDecMultiTaskModel.from_pretrained(model_id)
            except Exception as exc:
                raise AsrAdapterError(f"failed to load Canary {model_id}: {exc}") from exc
            self._loaded_model_id = model_id
            if vram_tracker:
                vram_tracker.measure_after_load()
        load_seconds = time.monotonic() - t0 if not model_reused else 0.0

        t1 = time.monotonic()
        try:
            # Canary uses transcribe() with source_lang and target_lang.
            # For transcription (not translation), source == target.
            result = self._model.transcribe(
                [str(audio_path)],
                source_lang=source_lang,
                target_lang=target_lang,
            )
        except Exception as exc:
            raise AsrAdapterError(f"Canary transcription failed: {exc}") from exc
        inference_seconds = time.monotonic() - t1

        text = ""
        segments: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        if isinstance(result, list) and len(result) > 0:
            entry = result[0]
            if isinstance(entry, dict):
                text = str(entry.get("text", "")).strip()
            elif isinstance(entry, str):
                text = entry.strip()

        peak_ram = measure_peak_ram()
        peak_vram = vram_tracker.peak_vram_bytes if vram_tracker else None

        return NormalizedTranscriptionResult(
            model_family="canary",
            model_id=model_id,
            model_revision=None,
            language=source_lang,
            language_probability=None,
            text=text,
            segments=segments,
            words=words,
            load_seconds=round(load_seconds, 3),
            inference_seconds=round(inference_seconds, 3),
            total_seconds=round(load_seconds + inference_seconds, 3),
            model_reused=model_reused,
            peak_vram_bytes=peak_vram,
            peak_ram_bytes=peak_ram,
            warnings=warnings,
        )

    def release(self) -> None:
        self._model = None
        self._loaded_model_id = None


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


_ADAPTERS: dict[str, type] = {
    "whisper": FasterWhisperAdapter,
    "parakeet": ParakeetAdapter,
    "canary": CanaryAdapter,
}


def get_adapter(model_family: str):
    cls = _ADAPTERS.get(model_family)
    if cls is None:
        raise AsrAdapterError(f"unknown model family: {model_family!r}")
    return cls()
