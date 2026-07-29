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
    visual_analysis: Path
    ideas_research: Path
    video_generation: Path
    editing: Path
    video_upscale: Path
    video_background_removal: Path
    video_text_edit: Path
    video_cut: Path
    rendering: Path
    editor_commands: Path

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
            visual_analysis=root / "visual_analysis",
            ideas_research=root / "ideas_research",
            video_generation=root / "video_generation",
            editing=root / "editing",
            video_upscale=root / "video_upscale",
            video_background_removal=root / "video_background_removal",
            video_text_edit=root / "video_text_edit",
            video_cut=root / "video_cut",
            rendering=root / "rendering",
            editor_commands=root / "editor_commands",
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
            self.visual_analysis,
            self.ideas_research,
            self.video_generation,
            self.editing,
            self.video_upscale,
            self.video_background_removal,
            self.video_text_edit,
            self.video_cut,
            self.rendering,
            self.editor_commands,
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
    temporary_asset_ttl_hours: float = field(
        default_factory=lambda: _env_float("TTVTURBO_TEMPORARY_ASSET_TTL_HOURS", 24.0)
    )

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
    # Default model: Qwen3-4B-Instruct-2507 (text-only, non-thinking mode).
    # The worker subprocess loads the model via transformers; the FastAPI
    # process never imports it. An operator can override the repo id via
    # TTVTURBO_CONVERSATION_MINING_MODEL_ID (e.g. to pin a revision), but
    # free-form model names from the frontend are never accepted.
    conversation_mining_model_id: str = field(default_factory=lambda: _env_str(
        "TTVTURBO_CONVERSATION_MINING_MODEL_ID",
        "Qwen/Qwen3-4B-Instruct-2507",
    ))
    conversation_mining_device: str = field(default_factory=lambda: _env_str("TTVTURBO_CONVERSATION_MINING_DEVICE", "cuda"))
    conversation_mining_dtype: str = field(default_factory=lambda: _env_str("TTVTURBO_CONVERSATION_MINING_DTYPE", "auto"))
    conversation_mining_max_new_tokens: int = field(default_factory=lambda: _env_int("TTVTURBO_CONVERSATION_MINING_MAX_NEW_TOKENS", 2048))
    # Conservative input cap for a 12 GB GPU. The model's full context
    # length is NOT used as the runtime default — a multi-hour VOD is
    # never fed into a single prompt (block building handles that).
    conversation_mining_max_input_tokens: int = field(default_factory=lambda: _env_int("TTVTURBO_CONVERSATION_MINING_MAX_INPUT_TOKENS", 8192))
    # Qwen3 supports a thinking mode; we disable it for structured mining
    # so the model returns JSON directly without <think> reasoning blocks.
    conversation_mining_thinking_enabled: bool = field(default_factory=lambda: _env_str(
        "TTVTURBO_CONVERSATION_MINING_THINKING", "0"
    ) in ("1", "true", "True", "TRUE"))
    conversation_mining_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_MINING", 1))
    conversation_mining_block_target_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_BLOCK_TARGET_SECONDS", 90.0))
    conversation_mining_block_max_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_BLOCK_MAX_SECONDS", 180.0))
    conversation_mining_block_overlap_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_BLOCK_OVERLAP_SECONDS", 15.0))
    conversation_mining_pause_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_CONVERSATION_MINING_PAUSE_SECONDS", 6.0))

    # --- visual analysis (layout / region detection) ----------------------
    # The vision model is applied only to sampled keyframes, never to every
    # frame.  An operator can override the model id via the env var, but
    # free-form model names from the frontend are never accepted.
    visual_analysis_model_id: str = field(default_factory=lambda: _env_optional_str("TTVTURBO_VISUAL_ANALYSIS_MODEL_ID") or "")
    visual_analysis_keyframe_interval_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_VISUAL_ANALYSIS_KEYFRAME_INTERVAL", 5.0))
    visual_analysis_layout_change_threshold: float = field(default_factory=lambda: _env_float("TTVTURBO_VISUAL_ANALYSIS_LAYOUT_CHANGE_THRESHOLD", 0.3))
    visual_analysis_template_validation_keyframes: int = field(default_factory=lambda: _env_int("TTVTURBO_VISUAL_ANALYSIS_TEMPLATE_VALIDATION_KEYFRAMES", 3))
    visual_analysis_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_VISUAL_ANALYSIS", 1))

    # --- ideas research (trend research -> video ideas -> scripts) --------
    # The research provider and the LLM are pluggable adapters (see
    # ttvturbo.ideas_research.providers).  An operator can pin a model id via
    # the env var; free-form model names from the frontend are never accepted.
    ideas_research_model_id: str = field(default_factory=lambda: _env_optional_str("TTVTURBO_IDEAS_RESEARCH_MODEL_ID") or "")
    ideas_research_thinking_model_id: str = field(default_factory=lambda: _env_optional_str("TTVTURBO_IDEAS_RESEARCH_THINKING_MODEL_ID") or "")
    ideas_research_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_IDEAS_RESEARCH", 1))
    # Default time range for a research run when the request omits it.
    ideas_research_default_time_range: str = field(default_factory=lambda: _env_str("TTVTURBO_IDEAS_RESEARCH_DEFAULT_TIME_RANGE", "7d"))
    # Maximum number of topics kept after clustering/scoring.
    ideas_research_default_max_topics: int = field(default_factory=lambda: _env_int("TTVTURBO_IDEAS_RESEARCH_DEFAULT_MAX_TOPICS", 20))
    # Reliability band thresholds (0..1) for source_confidence mapping.
    ideas_research_source_confidence_high: float = field(default_factory=lambda: _env_float("TTVTURBO_IDEAS_RESEARCH_SOURCE_CONFIDENCE_HIGH", 0.8))
    ideas_research_source_confidence_low: float = field(default_factory=lambda: _env_float("TTVTURBO_IDEAS_RESEARCH_SOURCE_CONFIDENCE_LOW", 0.4))
    # --- research provider keys / config ---
    # YouTube Data API v3 key (optional; without it the YouTube adapter
    # reports unavailable and the aggregator skips it).
    youtube_api_key: str = field(default_factory=lambda: _env_optional_str("YOUTUBE_API_KEY") or "")
    # X (Twitter) API v2 Bearer Token (optional; without it the Twitter/X
    # adapter reports unavailable and the aggregator skips it).
    x_bearer_token: str = field(default_factory=lambda: _env_optional_str("X_BEARER_TOKEN") or "")
    # TikTok Research API credentials (optional; without them the TikTok
    # adapter reports unavailable and the aggregator skips it).
    tiktok_client_key: str = field(default_factory=lambda: _env_optional_str("TIKTOK_CLIENT_KEY") or "")
    tiktok_client_secret: str = field(default_factory=lambda: _env_optional_str("TIKTOK_CLIENT_SECRET") or "")
    # Reddit OAuth2 credentials (optional; Reddit now requires OAuth2 for
    # all API access). Register at https://www.reddit.com/prefs/apps
    # (choose "script" type). Without them the Reddit adapter is skipped.
    reddit_client_id: str = field(default_factory=lambda: _env_optional_str("REDDIT_CLIENT_ID") or "")
    reddit_client_secret: str = field(default_factory=lambda: _env_optional_str("REDDIT_CLIENT_SECRET") or "")
    # Comma-separated list of subreddits for the Reddit adapter.
    ideas_research_subreddits: str = field(default_factory=lambda: _env_str("TTVTURBO_IDEAS_RESEARCH_SUBREDDITS", "gaming,Games,LivestreamFail,Twitch"))
    # Comma-separated list of RSS feed URLs for the RSS adapter.
    ideas_research_rss_feeds: str = field(default_factory=lambda: _env_str("TTVTURBO_IDEAS_RESEARCH_RSS_FEEDS", ""))
    # Which research providers to enable (comma-separated).  Default: all
    # keyless providers (rss, google_trends).  Add "reddit", "youtube",
    # "twitter", "tiktok" when their keys are configured.
    ideas_research_providers: str = field(default_factory=lambda: _env_str("TTVTURBO_IDEAS_RESEARCH_PROVIDERS", "rss,google_trends"))

    # --- video generation (diffusers CogVideoX worker) ---------------------
    # The video-generation worker runs in a separate subprocess and imports
    # diffusers / torch lazily. The FastAPI process never imports them.
    # Install requirements-gpu.txt (plus diffusers) to enable generation.
    # Without it the service reports `available=false` and rejects jobs.
    #
    # Concrete local adapter: diffusers CogVideoX family.
    #   * TEXT_TO_VIDEO  -> CogVideoXPipeline
    #   * IMAGE_TO_VIDEO -> CogVideoXImageToVideoPipeline
    # Defaults are empty so the base application starts without generation
    # dependencies and reports `available=false`.
    video_generation_t2v_model_id: str = field(default_factory=lambda: _env_optional_str("TTVTURBO_VIDEO_GENERATION_T2V_MODEL_ID") or "")
    video_generation_i2v_model_id: str = field(default_factory=lambda: _env_optional_str("TTVTURBO_VIDEO_GENERATION_I2V_MODEL_ID") or "")
    video_generation_device: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_GENERATION_DEVICE", "cuda"))
    video_generation_dtype: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_GENERATION_DTYPE", "bfloat16"))
    # Native CogVideoX frame rate.
    video_generation_fps: int = field(default_factory=lambda: _env_int("TTVTURBO_VIDEO_GENERATION_FPS", 8))
    # Conservative caps so a single job never runs unbounded.
    video_generation_max_duration_seconds: float = field(default_factory=lambda: _env_float("TTVTURBO_VIDEO_GENERATION_MAX_DURATION_SECONDS", 10.0))
    video_generation_max_prompt_length: int = field(default_factory=lambda: _env_int("TTVTURBO_VIDEO_GENERATION_MAX_PROMPT_LENGTH", 1000))
    video_generation_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_VIDEO_GENERATION", 1))

    # --- generic video upscale ---------------------------------------------
    # AUTO uses Real-ESRGAN NCNN when the executable is configured; otherwise
    # a deterministic Lanczos backend remains fully functional.
    video_upscale_backend: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_UPSCALE_BACKEND", "AUTO"))
    video_upscale_realesrgan_path: str = field(default_factory=lambda: _env_optional_str("TTVTURBO_REALESRGAN_PATH") or "")
    video_upscale_realesrgan_model: str = field(default_factory=lambda: _env_str("TTVTURBO_REALESRGAN_MODEL", "realesrgan-x4plus"))
    video_upscale_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_VIDEO_UPSCALE", 1))

    # --- generic video background removal ---------------------------------
    video_background_removal_model_id: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_BACKGROUND_MODEL", "isnet-general-use"))
    video_background_removal_person_model_id: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_BACKGROUND_PERSON_MODEL", "u2net_human_seg"))
    video_background_removal_device: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_BACKGROUND_DEVICE", "cpu"))
    video_background_removal_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_VIDEO_BACKGROUND_REMOVAL", 1))

    # --- text-guided video inpaint / edit ---------------------------------
    video_text_inpaint_model_id: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_INPAINT_MODEL", "stable-diffusion-v1-5/stable-diffusion-inpainting"))
    video_instruction_edit_model_id: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_INSTRUCTION_EDIT_MODEL", "timbrooks/instruct-pix2pix"))
    video_text_edit_device: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_TEXT_EDIT_DEVICE", "cuda"))
    video_text_edit_dtype: str = field(default_factory=lambda: _env_str("TTVTURBO_VIDEO_TEXT_EDIT_DTYPE", "float16"))
    video_text_edit_cache_dir: Optional[str] = field(default_factory=lambda: _env_optional_str("TTVTURBO_VIDEO_TEXT_EDIT_CACHE_DIR"))
    video_text_edit_max_processing_side: int = field(default_factory=lambda: _env_int("TTVTURBO_VIDEO_TEXT_EDIT_MAX_SIDE", 768))
    video_text_edit_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_VIDEO_TEXT_EDIT", 1))

    # --- video region cut (ausschneiden) ----------------------------------
    video_cut_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_VIDEO_CUT", 1))

    # --- deterministic preview / final rendering --------------------------
    rendering_max_concurrent: int = field(default_factory=lambda: _env_int("TTVTURBO_MAX_CONCURRENT_RENDERING", 1))

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
