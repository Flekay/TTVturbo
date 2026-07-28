"""Reusable multilingual ASR presets for faster-whisper.

A *preset* is a small, validated, pure-data configuration that captures
every parameter that influences a transcription result. Presets are **not**
plugin points and they do **not** store model objects — they are plain
dicts that can be persisted, compared and replayed.

The legacy ``legacy-current`` preset mirrors the *exact* runtime
configuration that ``media_processing.transcription_worker`` used before
this module existed. It is kept as a comparison baseline so a benchmark
can prove (or disprove) that a new multilingual preset actually changes
the output. The values are hard-coded from the worker defaults
(``large-v3``, ``cuda``, ``int8_float16``, ``language="de"``,
``condition_on_previous_text=True``, ``vad_filter=True``,
``beam_size=5``, ``word_timestamps=True``) — they are NOT read from
environment variables here, because a preset must be reproducible.

The other three presets are multilingual candidates that set
``language=None`` and ``multilingual=True`` so faster-whisper performs
automatic language detection per clip and keeps Denglish segments
intact instead of forcing German.

Selection of the production default is persisted in a small JSON file
under the data directory (``asr_default_preset.json``). The diagnostic
``multilingual-large-v3-no-vad`` preset can never be selected as the
default — it exists only to reveal whether VAD is removing real speech.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ttvturbo.media_processing.asr_presets")

SCHEMA_VERSION = 1

# faster-whisper parameters this codebase actually uses. Any preset that
# introduces a parameter not in this set is rejected at validation time so
# we never silently pass an unknown argument to an older library version.
KNOWN_TRANSCRIBE_PARAMS = frozenset({
    "language",
    "task",
    "beam_size",
    "word_timestamps",
    "condition_on_previous_text",
    "vad_filter",
    "vad_parameters",
    "hallucination_silence_threshold",
    "hotwords",
    "no_speech_threshold",
    "log_prob_threshold",
    "compression_ratio_threshold",
    "multilingual",
})

# Presets that may be selected as the production default. The diagnostic
# no-VAD preset is deliberately excluded.
PRODUCTION_ELIGIBLE_PRESET_IDS = frozenset({
    "legacy-current",
    "multilingual-large-v3-quality",
    "multilingual-large-v3-turbo",
})

DEFAULT_PRESET_FILE = "asr_default_preset.json"
FALLBACK_DEFAULT_PRESET_ID = "multilingual-large-v3-quality"


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class AsrPreset:
    """A validated, pure-data ASR configuration.

    The dataclass is the single source of truth. ``to_dict`` is used for
    persistence and for passing the configuration to the worker; no model
    object is ever stored in a preset.
    """

    id: str
    name: str
    description: str
    model: str
    device: str
    compute_type: str
    task: str = "transcribe"
    language: Optional[str] = None
    multilingual: bool = False
    beam_size: int = 5
    word_timestamps: bool = True
    condition_on_previous_text: bool = False
    vad_filter: bool = True
    vad_parameters: dict[str, Any] = field(default_factory=dict)
    hallucination_silence_threshold: Optional[float] = None
    hotwords: Optional[str] = None
    no_speech_threshold: Optional[float] = None
    log_prob_threshold: Optional[float] = None
    compression_ratio_threshold: Optional[float] = None
    # Marker so the UI/API can refuse to make a diagnostic preset the
    # production default without re-deriving the rule.
    production_eligible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def transcribe_kwargs(self) -> dict[str, Any]:
        """Return only the keys that map to ``WhisperModel.transcribe``.

        Omits ``None`` values so faster-whisper uses its own defaults for
        unspecified parameters. ``vad_parameters`` is only included when
        non-empty. ``multilingual`` is included only when ``True`` so the
        legacy preset (which never set it) stays byte-compatible with the
        previous behaviour.
        """
        out: dict[str, Any] = {
            "language": self.language,
            "task": self.task,
            "beam_size": self.beam_size,
            "word_timestamps": self.word_timestamps,
            "condition_on_previous_text": self.condition_on_previous_text,
            "vad_filter": self.vad_filter,
        }
        if self.vad_parameters:
            out["vad_parameters"] = dict(self.vad_parameters)
        if self.hallucination_silence_threshold is not None:
            out["hallucination_silence_threshold"] = self.hallucination_silence_threshold
        if self.hotwords:
            out["hotwords"] = self.hotwords
        if self.no_speech_threshold is not None:
            out["no_speech_threshold"] = self.no_speech_threshold
        if self.log_prob_threshold is not None:
            out["log_prob_threshold"] = self.log_prob_threshold
        if self.compression_ratio_threshold is not None:
            out["compression_ratio_threshold"] = self.compression_ratio_threshold
        if self.multilingual:
            out["multilingual"] = True
        return out


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

# ``legacy-current`` mirrors the exact runtime configuration used by
# ``media_processing.transcription_worker`` before the preset system was
# introduced. The values come from the worker defaults
# (DEFAULT_MODEL/DEVICE/COMPUTE_TYPE/LANGUAGE) and the hard-coded
# transcribe() call (beam_size=5, word_timestamps=True,
# condition_on_previous_text=True, vad_filter=True). It does NOT set
# ``multilingual`` and does NOT set ``hallucination_silence_threshold``
# because the legacy worker never passed those.
LEGACY_CURRENT = AsrPreset(
    id="legacy-current",
    name="Aktuelle Konfiguration (Legacy)",
    description=(
        "Exakte Abbildung der bisherigen produktiven Transkription: "
        "large-v3, int8_float16, Sprache erzwungen auf Deutsch, "
        "condition_on_previous_text an, VAD an. Vergleichsbasis."
    ),
    model="large-v3",
    device="cuda",
    compute_type="int8_float16",
    task="transcribe",
    language="de",
    multilingual=False,
    beam_size=5,
    word_timestamps=True,
    condition_on_previous_text=True,
    vad_filter=True,
    vad_parameters={},
    hallucination_silence_threshold=None,
    hotwords=None,
    production_eligible=True,
)

MULTILINGUAL_LARGE_V3_QUALITY = AsrPreset(
    id="multilingual-large-v3-quality",
    name="Large v3 – Multilingual Quality",
    description=(
        "large-v3, float16, automatische Spracherkennung (language=None), "
        "multilingual an, condition_on_previous_text aus, VAD an, "
        "hallucination_silence_threshold=1.0. Neuer Qualitätskandidat für "
        "Denglish-Clips."
    ),
    model="large-v3",
    device="cuda",
    compute_type="float16",
    task="transcribe",
    language=None,
    multilingual=True,
    beam_size=5,
    word_timestamps=True,
    condition_on_previous_text=False,
    vad_filter=True,
    vad_parameters={},
    hallucination_silence_threshold=1.0,
    hotwords=None,
    production_eligible=True,
)

MULTILINGUAL_LARGE_V3_NO_VAD = AsrPreset(
    id="multilingual-large-v3-no-vad",
    name="Large v3 – Multilingual ohne VAD (Diagnose)",
    description=(
        "Wie Multilingual Quality, aber VAD aus. Ausschließlich diagnostisch: "
        "zeigt, ob scheinbar fehlende Sprache durch den VAD-Filter entfernt "
        "wurde. Darf NICHT als Produktionsstandard gewählt werden."
    ),
    model="large-v3",
    device="cuda",
    compute_type="float16",
    task="transcribe",
    language=None,
    multilingual=True,
    beam_size=5,
    word_timestamps=True,
    condition_on_previous_text=False,
    vad_filter=False,
    vad_parameters={},
    hallucination_silence_threshold=1.0,
    hotwords=None,
    production_eligible=False,
)

MULTILINGUAL_LARGE_V3_TURBO = AsrPreset(
    id="multilingual-large-v3-turbo",
    name="Large v3 Turbo – Multilingual",
    description=(
        "large-v3-turbo, int8_float16, automatische Spracherkennung, multilingual "
        "an, VAD an, beam_size=1. Vergleicht Geschwindigkeit und Qualität gegen Large v3."
    ),
    model="large-v3-turbo",
    device="cuda",
    compute_type="int8_float16",
    task="transcribe",
    language=None,
    multilingual=True,
    beam_size=1,
    word_timestamps=True,
    condition_on_previous_text=False,
    vad_filter=True,
    vad_parameters={},
    hallucination_silence_threshold=1.0,
    hotwords=None,
    production_eligible=True,
)

BUILTIN_PRESETS: dict[str, AsrPreset] = {
    p.id: p for p in (
        LEGACY_CURRENT,
        MULTILINGUAL_LARGE_V3_QUALITY,
        MULTILINGUAL_LARGE_V3_NO_VAD,
        MULTILINGUAL_LARGE_V3_TURBO,
    )
}


class AsrPresetError(Exception):
    """Preset validation or selection error."""


class AsrPresetNotFoundError(AsrPresetError):
    """A preset with the given id does not exist."""


def _validate(preset: AsrPreset) -> AsrPreset:
    if not preset.id or not isinstance(preset.id, str):
        raise AsrPresetError("preset id must be a non-empty string")
    if not preset.name or not isinstance(preset.name, str):
        raise AsrPresetError("preset name must be a non-empty string")
    if not preset.model or not isinstance(preset.model, str):
        raise AsrPresetError("preset model must be a non-empty string")
    if preset.task not in ("transcribe", "translate"):
        raise AsrPresetError(f"unsupported task {preset.task!r}")
    if preset.device not in ("cuda", "cpu") and not preset.device.startswith("cuda"):
        raise AsrPresetError(f"unsupported device {preset.device!r}")
    if not isinstance(preset.vad_parameters, dict):
        raise AsrPresetError("vad_parameters must be a dict")
    # Reject parameters we do not know how to forward, so a stale preset
    # file can never silently drop a key.
    for key in preset.vad_parameters:
        if key not in {"threshold", "neg_threshold", "min_speech_duration_ms",
                       "max_speech_duration_s", "min_silence_duration_ms",
                       "speech_pad_ms"}:
            raise AsrPresetError(f"unknown vad_parameter {key!r}")
    return preset


def get_preset(preset_id: str) -> AsrPreset:
    """Return a validated built-in preset by id."""
    preset = BUILTIN_PRESETS.get(preset_id)
    if preset is None:
        raise AsrPresetNotFoundError(f"unknown preset: {preset_id!r}")
    return _validate(preset)


def list_presets() -> list[dict[str, Any]]:
    """Return all built-in presets as dicts, sorted by a stable order."""
    order = list(BUILTIN_PRESETS.keys())
    return [BUILTIN_PRESETS[i].to_dict() for i in order]


def is_production_eligible(preset_id: str) -> bool:
    preset = BUILTIN_PRESETS.get(preset_id)
    if preset is None:
        return False
    return bool(preset.production_eligible) and preset_id in PRODUCTION_ELIGIBLE_PRESET_IDS


# ---------------------------------------------------------------------------
# Default-preset persistence
# ---------------------------------------------------------------------------


class AsrDefaultPresetStore:
    """Persist the currently selected production default preset.

    The store is a single JSON file under the data directory. It records
    the preset id, the full effective configuration at selection time and
    a timestamp. Old transcriptions are never modified by a selection
    change — the selection only affects *new* jobs.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / DEFAULT_PRESET_FILE

    def _read(self) -> Optional[dict[str, Any]]:
        if not self.path.is_file():
            return None
        try:
            with open(self.path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("could not read default preset file %s: %s", self.path, exc)
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def get(self) -> dict[str, Any]:
        """Return the current default selection.

        Falls back to ``FALLBACK_DEFAULT_PRESET_ID`` (without writing the
        file) when no selection has been persisted yet or the persisted
        selection is no longer eligible (e.g. the preset was removed in a
        newer release).
        """
        payload = self._read()
        if payload is not None:
            pid = payload.get("preset_id")
            if pid and is_production_eligible(pid):
                preset = get_preset(pid)
                return {
                    "preset_id": pid,
                    "preset": preset.to_dict(),
                    "selected_at": payload.get("selected_at"),
                }
        preset = get_preset(FALLBACK_DEFAULT_PRESET_ID)
        return {
            "preset_id": FALLBACK_DEFAULT_PRESET_ID,
            "preset": preset.to_dict(),
            "selected_at": None,
        }

    def get_preset(self) -> AsrPreset:
        """Return the resolved default :class:`AsrPreset`."""
        return get_preset(self.get()["preset_id"])

    def select(self, preset_id: str) -> dict[str, Any]:
        """Persist a new production default.

        Raises :class:`AsrPresetNotFoundError` if the preset is unknown and
        :class:`AsrPresetError` if the preset is known but not
        production-eligible (e.g. the diagnostic no-VAD preset).
        """
        # Distinguish "unknown" from "known but ineligible" so the API
        # can return 404 vs 400 respectively.
        if preset_id not in BUILTIN_PRESETS:
            raise AsrPresetNotFoundError(f"unknown preset: {preset_id!r}")
        if not is_production_eligible(preset_id):
            raise AsrPresetError(
                f"preset {preset_id!r} is not eligible as production default"
            )
        preset = get_preset(preset_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "preset_id": preset_id,
            "preset": preset.to_dict(),
            "selected_at": _now_iso(),
        }
        tmp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, self.path)
        return payload


# ---------------------------------------------------------------------------
# faster-whisper compatibility check
# ---------------------------------------------------------------------------


def faster_whisper_version() -> Optional[str]:
    """Return the installed faster-whisper version, or None if unavailable."""
    try:
        import faster_whisper  # type: ignore[import-not-found]
        return getattr(faster_whisper, "__version__", None)
    except Exception:
        return None


def supported_transcribe_params() -> set[str]:
    """Return the set of ``WhisperModel.transcribe`` parameter names.

    Returns an empty set if faster-whisper is not importable. Used by the
    benchmark worker to refuse to forward a parameter the installed
    version does not understand (no silent fallback).
    """
    try:
        import inspect  # noqa: PLC0415
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        sig = inspect.signature(WhisperModel.transcribe)
        return set(sig.parameters.keys()) - {"self"}
    except Exception:
        return set()


def check_preset_compatibility(preset: AsrPreset) -> list[str]:
    """Return a list of incompatibility reasons for ``preset``.

    Empty list means the installed faster-whisper supports every
    parameter the preset wants to forward. This is called at runtime so
    we never silently drop a parameter on an older library version.
    """
    reasons: list[str] = []
    available = supported_transcribe_params()
    if not available:
        reasons.append("faster-whisper is not installed")
        return reasons
    for key in preset.transcribe_kwargs():
        if key not in available:
            reasons.append(
                f"installed faster-whisper does not support parameter {key!r}"
            )
    return reasons
