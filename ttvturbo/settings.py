"""Centralized, typed settings and data-path structure for TTVturbo.

This module is the single source of truth for all configurable values and
filesystem paths.  Importing it has **no side effects** -- no directories are
created, no environment is mutated, no services are constructed.

``Settings`` consolidates every knob that was previously scattered across
``os.environ.get(...)`` calls in ``app.py`` and the individual service
modules.  ``DataPaths`` derives every persistent artifact directory from a
single ``data_root`` so services never assemble contradictory paths.

Use ``Settings.from_env()`` for production (reads ``TTVTURBO_DATA_DIR`` and
friends) or construct ``Settings`` directly in tests with a temporary
``data_root``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

APP_NAME = "TTVturbo"
APP_VERSION = "0.1.0"

# Upload guardrails (mirrors the previous module-level constants in app.py).
MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MiB
ALLOWED_UPLOAD_MIME_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/aac",
    "application/octet-stream",
}
ALLOWED_UPLOAD_EXTENSIONS = {".webm", ".ogg", ".mp4", ".m4a", ".mp3", ".wav", ".aac"}


# ---------------------------------------------------------------------------
# DataPaths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataPaths:
    """Every persistent artifact directory, derived from a single root.

    Services receive the relevant ``Path`` from this structure instead of
    assembling their own.  The structure is **frozen** so it can be safely
    shared across services without accidental mutation.
    """

    data_root: Path
    recordings: Path
    voice_clones: Path
    voice_profiles: Path
    twitch_profiles: Path
    vods: Path
    library: Path
    media_jobs: Path
    pipeline_runs: Path
    uploads: Path
    asr_benchmarks: Path
    asr_diagnostics: Path
    asr_default_preset: Path

    @classmethod
    def from_root(cls, data_root: Path) -> "DataPaths":
        """Build the full path tree from a single data root."""
        root = Path(data_root)
        return cls(
            data_root=root,
            recordings=root / "recordings",
            voice_clones=root / "voice_clones",
            voice_profiles=root / "voice_profiles",
            twitch_profiles=root / "twitch_profiles",
            vods=root / "vods",
            library=root / "library",
            media_jobs=root / "media_jobs",
            pipeline_runs=root / "pipeline_runs",
            uploads=root / "uploads",
            asr_benchmarks=root / "asr_benchmarks",
            asr_diagnostics=root / "audio_diagnostics",
            asr_default_preset=root,
        )

    def ensure_dirs(self) -> None:
        """Create every directory in the tree (idempotent).

        Called by the app lifespan **after** settings are finalized, never
        at import time.
        """
        for p in (
            self.data_root,
            self.recordings,
            self.voice_clones,
            self.voice_profiles,
            self.twitch_profiles,
            self.vods,
            self.library,
            self.media_jobs,
            self.pipeline_runs,
            self.uploads,
            self.asr_benchmarks,
            self.asr_diagnostics,
        ):
            p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _env_path(name: str, default: Path) -> Path:
    val = os.environ.get(name)
    return Path(val) if val else default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_optional_str(name: str) -> Optional[str]:
    return os.environ.get(name)


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or not val.isdigit():
        return default
    return int(val)


@dataclass
class Settings:
    """Typed, centralized configuration for the whole application.

    Construct via ``Settings.from_env()`` for production or directly in
    tests.  No field triggers I/O at construction time.
    """

    # --- filesystem --------------------------------------------------------
    data_root: Path = field(default_factory=lambda: _env_path("TTVTURBO_DATA_DIR", DEFAULT_DATA_DIR))
    frontend_dist: Path = field(default_factory=lambda: DEFAULT_FRONTEND_DIST)

    # --- external tools ----------------------------------------------------
    ffmpeg_path: Optional[str] = None
    ffprobe_path: Optional[str] = None
    yt_dlp: Optional[str] = None

    # --- twitch credentials (read-only, never persisted) -------------------
    twitch_client_id: Optional[str] = field(default_factory=lambda: _env_optional_str("TTVTURBO_TWITCH_CLIENT_ID"))
    twitch_client_secret: Optional[str] = field(default_factory=lambda: _env_optional_str("TTVTURBO_TWITCH_CLIENT_SECRET"))

    # --- worker / subprocess -----------------------------------------------
    worker_python: str = field(default_factory=lambda: sys.executable)

    # --- GPU lock ----------------------------------------------------------
    gpu_lock_stale_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_GPU_LOCK_STALE_SECONDS", 3600.0))

    # --- voice clone (Qwen3-TTS) -------------------------------------------
    voice_clone_model_id: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    voice_clone_device: str = "cuda:0"
    voice_clone_dtype: str = "bfloat16"
    voice_clone_timeout_seconds: Optional[float] = field(default_factory=lambda: _env_optional_str("TTVTURBO_VOICE_CLONE_TIMEOUT_SECONDS") and _env_float("TTVTURBO_VOICE_CLONE_TIMEOUT_SECONDS", 300.0))

    # --- transcription (faster-whisper) ------------------------------------
    transcription_model: str = field(default_factory=lambda: _env_str("TTVTURBO_TRANSCRIPTION_MODEL", "large-v3"))
    transcription_device: str = field(default_factory=lambda: _env_str("TTVTURBO_TRANSCRIPTION_DEVICE", "cuda"))
    transcription_compute_type: str = field(default_factory=lambda: _env_str("TTVTURBO_TRANSCRIPTION_COMPUTE_TYPE", "int8_float16"))
    transcription_language: str = field(default_factory=lambda: _env_str("TTVTURBO_TRANSCRIPTION_LANGUAGE", "de"))
    transcription_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_TRANSCRIPTIONS", 1))

    # --- VOD pipeline ------------------------------------------------------
    vod_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_VOD_DOWNLOADS", 1))
    vod_download_timeout_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_VOD_DOWNLOAD_TIMEOUT_SECONDS", 0.0))
    vod_sync_limit: int = field(default_factory=lambda: _env_int("TTVTURBO_VOD_SYNC_LIMIT", 100))

    # --- conversation mining (local text LLM) ------------------------------
    # No model is configured by default — the service reports UNAVAILABLE
    # until an operator sets TTVTURBO_CONVERSATION_MINING_MODEL_ID to a
    # HuggingFace repo id. The worker subprocess loads the model via
    # transformers; the FastAPI process never imports it.
    conversation_mining_model_id: Optional[str] = field(default_factory=lambda: _env_optional_str("TTVTURBO_CONVERSATION_MINING_MODEL_ID"))
    conversation_mining_device: str = field(default_factory=lambda: _env_str("TTVTURBO_CONVERSATION_MINING_DEVICE", "cuda"))
    conversation_mining_dtype: str = field(default_factory=lambda: _env_str("TTVTURBO_CONVERSATION_MINING_DTYPE", "auto"))
    conversation_mining_max_new_tokens: int = field(default_factory=lambda: _env_int("TTVTURBO_CONVERSATION_MINING_MAX_NEW_TOKENS", 2048))
    conversation_mining_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_MINING", 1))
    conversation_mining_block_target_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_BLOCK_TARGET_SECONDS", 90.0))
    conversation_mining_block_max_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_BLOCK_MAX_SECONDS", 180.0))
    conversation_mining_block_overlap_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_BLOCK_OVERLAP_SECONDS", 15.0))
    conversation_mining_pause_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_PAUSE_SECONDS", 6.0))

    # --- server ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8765

    # --- ASR / voice model paths and caches --------------------------------
    # These are read by the ASR subsystem.  Empty strings mean "use the
    # tool's default download/cache location" (e.g. HuggingFace cache).
    asr_model_cache_dir: Optional[str] = field(default_factory=lambda: _env_optional_str("TTVTURBO_ASR_MODEL_CACHE_DIR"))
    voice_model_cache_dir: Optional[str] = field(default_factory=lambda: _env_optional_str("TTVTURBO_VOICE_MODEL_CACHE_DIR"))

    # --- upload limits -----------------------------------------------------
    max_upload_bytes: int = MAX_UPLOAD_BYTES

    # --- derived -----------------------------------------------------------
    _paths: Optional[DataPaths] = field(default=None, repr=False, compare=False)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables with local defaults."""
        return cls()

    def paths(self) -> DataPaths:
        """Return the :class:`DataPaths` derived from ``data_root``.

        The result is cached so every caller sees the same object.
        """
        if self._paths is None:
            object.__setattr__(self, "_paths", DataPaths.from_root(self.data_root))
        return self._paths  # type: ignore[return-value]
