"""FastAPI application factory with centralized settings and test isolation.

Importing this module has **no side effects** — no directories are created, no
services are constructed, no jobs are recovered.  All of that happens inside
the :func:`create_app` lifespan when the server (or ``TestClient``) starts.

``create_app(settings, overrides)`` returns a fully wired ``FastAPI`` instance.
Route handlers read from a :class:`ServiceContainer` that is populated by the
lifespan, so the app object can be created without touching the filesystem.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ttvturbo.settings import (
    APP_NAME,
    DataPaths,
    Settings,
)

from ttvturbo.voice_clone.service import VoiceCloneService

from ttvturbo.voice_profiles_api import (
    build_router as build_voice_profiles_router,
    build_service as build_voice_profile_service,
    make_quality_analyzer as make_voice_profile_quality_analyzer,
)

from ttvturbo.vod_pipeline_api import (
    build_router as build_vod_pipeline_router,
    build_service as build_vod_pipeline_service,
    build_twitch_status_router,
)

from ttvturbo.media_processing import (
    AsrBenchmarkService,
    AsrDefaultPresetStore,
    AudioExtractionService,
    AudioForensicsService,
    ConversationMiningService,
    EditorCommandService,
    GpuLock,
    MediaJobStorage,
    MediaSourceResolver,
    PipelineService,
    TranscriptionService,
    UploadStorage,
)
from ttvturbo.media_processing_api import build_media_processing_router
from ttvturbo.conversation_mining_api import build_conversation_mining_router
from ttvturbo.editor_command_api import build_editor_command_router
from ttvturbo.asr_api import build_asr_router
from ttvturbo.visual_analysis_api import build_visual_analysis_router
from ttvturbo.video_generation_api import build_video_generation_router
from ttvturbo.ideas_research_api import build_ideas_research_router
from ttvturbo.editing_api import build_editing_router
from ttvturbo.video_upscale_api import build_video_upscale_router
from ttvturbo.video_background_removal_api import build_video_background_removal_router
from ttvturbo.video_text_edit_api import build_video_text_edit_router
from ttvturbo.video_cut_api import build_video_cut_router
from ttvturbo.rendering_api import build_rendering_router
from ttvturbo.editing import EditDatabase, EditProjectService

from ttvturbo.library import LibraryService, LibraryStorage
from ttvturbo.library_api import build_library_router

logger = logging.getLogger("ttvturbo")


# ---------------------------------------------------------------------------
# Service container and lazy proxy
# ---------------------------------------------------------------------------


class ServiceContainer:
    """Mutable container for all service instances.

    Populated by the :func:`create_app` lifespan.  Route handlers access
    services through :class:`_ServiceProxy` instances that delegate to this
    container, so the app can be constructed before any service exists.
    """

    def __init__(self) -> None:
        self.settings: Optional[Settings] = None
        self.paths: Optional[DataPaths] = None
        self.gpu_lock: Any = None
        self.library_storage: Any = None
        self.library_service: Any = None
        self.voice_clone_service: Any = None
        self.voice_profile_service: Any = None
        self.vod_pipeline_service: Any = None
        self.media_job_storage: Any = None
        self.upload_storage: Any = None
        self.media_source_resolver: Any = None
        self.audio_extraction_service: Any = None
        self.asr_default_preset_store: Any = None
        self.transcription_service: Any = None
        self.pipeline_service: Any = None
        self.mining_service: Any = None
        self.editor_command_service: Any = None
        self.asr_benchmark_service: Any = None
        self.audio_forensics_service: Any = None
        self.visual_analysis_service: Any = None
        self.video_generation_service: Any = None
        self.ideas_research_service: Any = None
        self.edit_project_service: Any = None
        self.video_upscale_service: Any = None
        self.video_background_removal_service: Any = None
        self.video_text_edit_service: Any = None
        self.video_cut_service: Any = None
        self.rendering_service: Any = None
        # Router references (for tests that need to swap router.state).
        self.voice_profiles_router: Any = None
        self.vod_pipeline_router: Any = None
        self.twitch_status_router: Any = None
        self.media_processing_router: Any = None
        self.conversation_mining_router: Any = None
        self.editor_command_router: Any = None
        self.library_router: Any = None
        self.asr_router: Any = None
        self.visual_analysis_router: Any = None
        self.video_generation_router: Any = None
        self.ideas_research_router: Any = None
        self.editing_router: Any = None
        self.video_upscale_router: Any = None
        self.video_background_removal_router: Any = None
        self.video_text_edit_router: Any = None
        self.video_cut_router: Any = None
        self.rendering_router: Any = None
        self.app_router: Any = None
        self.start_time_monotonic: float = 0.0


class _ServiceProxy:
    """Lazy proxy that delegates attribute access to a container attribute.

    The proxy is passed to ``build_router()`` factories in place of a real
    service.  Method calls are forwarded to the real service once the lifespan
    has populated the container.
    """

    def __init__(self, container: ServiceContainer, attr: str) -> None:
        object.__setattr__(self, "_container", container)
        object.__setattr__(self, "_attr", attr)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._container, self._attr)
        if target is None:
            raise RuntimeError(
                f"Service '{self._attr}' not initialized — "
                "app lifespan has not started"
            )
        return getattr(target, name)


@dataclass
class ServiceOverrides:
    """Optional fakes/stubs to replace specific services in tests.

    When a field is not ``None``, the lifespan uses the provided instance
    instead of constructing a real one.  This lets tests inject lightweight
    fakes without touching the filesystem.
    """

    gpu_lock: Any = None
    library_service: Any = None
    voice_clone_service: Any = None
    voice_profile_service: Any = None
    vod_pipeline_service: Any = None
    media_job_storage: Any = None
    upload_storage: Any = None
    media_source_resolver: Any = None
    audio_extraction_service: Any = None
    asr_default_preset_store: Any = None
    transcription_service: Any = None
    pipeline_service: Any = None
    mining_service: Any = None
    editor_command_service: Any = None
    asr_benchmark_service: Any = None
    audio_forensics_service: Any = None
    visual_analysis_service: Any = None
    video_generation_service: Any = None
    ideas_research_service: Any = None
    edit_project_service: Any = None
    video_upscale_service: Any = None
    video_background_removal_service: Any = None
    video_text_edit_service: Any = None
    video_cut_service: Any = None
    rendering_service: Any = None


# ---------------------------------------------------------------------------
# Service initialisation (called by the lifespan)
# ---------------------------------------------------------------------------


def _init_services(
    container: ServiceContainer,
    settings: Settings,
    overrides: Optional[ServiceOverrides],
) -> None:
    """Construct all services and populate *container*.

    Called from the FastAPI lifespan — never at import time.
    """
    paths = settings.paths()
    container.paths = paths
    paths.ensure_dirs()

    ov = overrides

    # Resolve external tool paths once from the central Settings so every
    # service and worker subprocess uses the same FFmpeg / FFprobe / yt-dlp
    # / Python interpreter. Explicit Settings values always win; otherwise
    # ExecutableResolver falls back to PATH + Windows install-location lookup.
    from ttvturbo.system.executables import resolve_tools

    tools = resolve_tools(settings)

    # --- GPU lock ---------------------------------------------------------
    if ov and ov.gpu_lock is not None:
        container.gpu_lock = ov.gpu_lock
    else:
        container.gpu_lock = GpuLock(paths.data_root)

    # --- Library ----------------------------------------------------------
    if ov and ov.library_service is not None:
        container.library_service = ov.library_service
    else:
        container.library_storage = LibraryStorage(paths.library)
        container.library_service = LibraryService(
            container.library_storage,
            temporary_ttl_hours=settings.temporary_asset_ttl_hours,
        )

    # --- Edit projects ----------------------------------------------------
    if ov and ov.edit_project_service is not None:
        container.edit_project_service = ov.edit_project_service
    else:
        container.edit_project_service = EditProjectService(
            EditDatabase(paths.editing / "edit_projects.sqlite3"),
            library_service=container.library_service,
        )

    # --- Voice clone ------------------------------------------------------
    if ov and ov.voice_clone_service is not None:
        container.voice_clone_service = ov.voice_clone_service
    else:
        container.voice_clone_service = VoiceCloneService(
            recordings_dir=paths.recordings,
            voice_clones_dir=paths.voice_clones,
            gpu_lock=container.gpu_lock,
            settings=settings,
            worker_python=tools.python,
        )

    # --- Voice profiles ---------------------------------------------------
    if ov and ov.voice_profile_service is not None:
        container.voice_profile_service = ov.voice_profile_service
    else:
        container.voice_profile_service = build_voice_profile_service(
            recordings_dir=paths.recordings,
            voice_profiles_dir=paths.voice_profiles,
        )

    # --- VOD pipeline -----------------------------------------------------
    if ov and ov.vod_pipeline_service is not None:
        container.vod_pipeline_service = ov.vod_pipeline_service
    else:
        container.vod_pipeline_service = build_vod_pipeline_service(
            data_dir=paths.data_root,
            download_dir=paths.vods,
            library_service=container.library_service,
            settings=settings,
        )

    # --- Media processing -------------------------------------------------
    if ov and ov.media_job_storage is not None:
        container.media_job_storage = ov.media_job_storage
    else:
        container.media_job_storage = MediaJobStorage(paths.data_root)

    if ov and ov.upload_storage is not None:
        container.upload_storage = ov.upload_storage
    else:
        container.upload_storage = UploadStorage(paths.uploads)

    if ov and ov.media_source_resolver is not None:
        container.media_source_resolver = ov.media_source_resolver
    else:
        container.media_source_resolver = MediaSourceResolver(
            container.vod_pipeline_service.storage,
            upload_storage=container.upload_storage,
            library_service=container.library_service,
        )

    if ov and ov.audio_extraction_service is not None:
        container.audio_extraction_service = ov.audio_extraction_service
    else:
        container.audio_extraction_service = AudioExtractionService(
            storage=container.media_job_storage,
            source_resolver=container.media_source_resolver,
            settings=settings,
            worker_python=tools.python,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
        )

    if ov and ov.asr_default_preset_store is not None:
        container.asr_default_preset_store = ov.asr_default_preset_store
    else:
        container.asr_default_preset_store = AsrDefaultPresetStore(paths.data_root)

    if ov and ov.transcription_service is not None:
        container.transcription_service = ov.transcription_service
    else:
        container.transcription_service = TranscriptionService(
            storage=container.media_job_storage,
            source_resolver=container.media_source_resolver,
            audio_service=container.audio_extraction_service,
            gpu_lock=container.gpu_lock,
            default_preset_store=container.asr_default_preset_store,
            settings=settings,
            worker_python=tools.python,
        )

    # Wire the audio-ready callback after both services exist.
    if container.audio_extraction_service is not None and container.transcription_service is not None:
        container.audio_extraction_service._on_job_ready = (  # noqa: SLF001
            container.transcription_service.on_audio_ready
        )

    # --- Conversation mining ---------------------------------------------
    if ov and ov.mining_service is not None:
        container.mining_service = ov.mining_service
    else:
        container.mining_service = ConversationMiningService(
            transcription_service=container.transcription_service,
            gpu_lock=container.gpu_lock,
            settings=settings,
            worker_python=tools.python,
        )

    # --- Editor command parser (local text LLM, reuses mining model) ------
    if ov and ov.editor_command_service is not None:
        container.editor_command_service = ov.editor_command_service
    else:
        container.editor_command_service = EditorCommandService(
            gpu_lock=container.gpu_lock,
            settings=settings,
            worker_python=tools.python,
        )

    if ov and ov.pipeline_service is not None:
        container.pipeline_service = ov.pipeline_service
    else:
        container.pipeline_service = PipelineService(
            storage=container.media_job_storage,
            vod_service=container.vod_pipeline_service,
            audio_service=container.audio_extraction_service,
            transcription_service=container.transcription_service,
            mining_service=container.mining_service,
        )

    # --- ASR benchmark / forensics ---------------------------------------
    if ov and ov.asr_benchmark_service is not None:
        container.asr_benchmark_service = ov.asr_benchmark_service
    else:
        container.asr_benchmark_service = AsrBenchmarkService(
            data_dir=paths.data_root,
            source_resolver=container.media_source_resolver,
            gpu_lock=container.gpu_lock,
        )

    if ov and ov.audio_forensics_service is not None:
        container.audio_forensics_service = ov.audio_forensics_service
    else:
        container.audio_forensics_service = AudioForensicsService(
            data_dir=paths.data_root,
            source_resolver=container.media_source_resolver,
        )

    # --- Visual analysis --------------------------------------------------
    if ov and ov.visual_analysis_service is not None:
        container.visual_analysis_service = ov.visual_analysis_service
    else:
        from ttvturbo.visual_analysis import (
            UnavailableVisionAdapter,
            VisualAnalysisService,
            VisualAnalysisStorage,
        )
        va_storage = VisualAnalysisStorage(paths.visual_analysis)
        container.visual_analysis_service = VisualAnalysisService(
            storage=va_storage,
            source_resolver=container.media_source_resolver,
            settings=settings,
            library_service=container.library_service,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
        )

    # --- Ideas research ---------------------------------------------------
    if ov and ov.ideas_research_service is not None:
        container.ideas_research_service = ov.ideas_research_service
    else:
        from ttvturbo.ideas_research import (
            AggregatingResearchProvider,
            GoogleTrendsResearchProvider,
            IdeasResearchService,
            IdeasResearchStorage,
            RedditResearchProvider,
            RssResearchProvider,
            TikTokResearchProvider,
            TwitterXResearchProvider,
            UnavailableLLMAdapter,
            YouTubeResearchProvider,
        )
        ir_storage = IdeasResearchStorage(paths.ideas_research)
        # Build the research provider chain from settings.  Only the
        # providers listed in ``ideas_research_providers`` are wired; each
        # gracefully reports unavailable when its prerequisites are
        # missing (e.g. no API key).
        enabled = {
            p.strip().lower()
            for p in (settings.ideas_research_providers or "").split(",")
            if p.strip()
        } or {"reddit", "rss", "google_trends"}
        research_providers: list[Any] = []
        if "reddit" in enabled:
            subs = tuple(
                s.strip() for s in (settings.ideas_research_subreddits or "").split(",") if s.strip()
            ) or ("gaming", "Games", "LivestreamFail", "Twitch")
            research_providers.append(RedditResearchProvider(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                subreddits=subs,
            ))
        if "rss" in enabled:
            feeds = tuple(
                f.strip() for f in (settings.ideas_research_rss_feeds or "").split(",") if f.strip()
            )
            research_providers.append(RssResearchProvider(feeds=feeds))
        if "google_trends" in enabled:
            research_providers.append(GoogleTrendsResearchProvider())
        if "youtube" in enabled and settings.youtube_api_key:
            research_providers.append(YouTubeResearchProvider(api_key=settings.youtube_api_key))
        if "twitter" in enabled and settings.x_bearer_token:
            research_providers.append(TwitterXResearchProvider(bearer_token=settings.x_bearer_token))
        if "tiktok" in enabled and settings.tiktok_client_key and settings.tiktok_client_secret:
            research_providers.append(TikTokResearchProvider(
                client_key=settings.tiktok_client_key,
                client_secret=settings.tiktok_client_secret,
            ))
        research_provider: Any
        if research_providers:
            research_provider = AggregatingResearchProvider(research_providers)
        else:
            from ttvturbo.ideas_research import UnavailableResearchProvider
            research_provider = UnavailableResearchProvider()
        container.ideas_research_service = IdeasResearchService(
            storage=ir_storage,
            settings=settings,
            research_provider=research_provider,
            llm_adapter=UnavailableLLMAdapter(),
        )

    # --- Video generation -------------------------------------------------
    if ov and ov.video_generation_service is not None:
        container.video_generation_service = ov.video_generation_service
    else:
        from ttvturbo.video_generation import (
            UnavailableVideoGenerationAdapter,
            VideoGenerationService,
            VideoGenerationStorage,
        )
        vg_storage = VideoGenerationStorage(paths.video_generation)
        container.video_generation_service = VideoGenerationService(
            storage=vg_storage,
            source_resolver=container.media_source_resolver,
            settings=settings,
            gpu_lock=container.gpu_lock,
            library_service=container.library_service,
            worker_python=tools.python,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
            adapter=UnavailableVideoGenerationAdapter(),
        )

    # --- Generic media capabilities ---------------------------------------
    if ov and ov.video_upscale_service is not None:
        container.video_upscale_service = ov.video_upscale_service
    else:
        from ttvturbo.video_upscale import VideoUpscaleService, VideoUpscaleStorage

        container.video_upscale_service = VideoUpscaleService(
            storage=VideoUpscaleStorage(paths.video_upscale),
            library_service=container.library_service,
            settings=settings,
            gpu_lock=container.gpu_lock,
            visual_analysis_service=container.visual_analysis_service,
            worker_python=tools.python,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
        )

    if ov and ov.video_background_removal_service is not None:
        container.video_background_removal_service = ov.video_background_removal_service
    else:
        from ttvturbo.video_background_removal import (
            VideoBackgroundRemovalService,
            VideoBackgroundRemovalStorage,
        )

        container.video_background_removal_service = VideoBackgroundRemovalService(
            storage=VideoBackgroundRemovalStorage(paths.video_background_removal),
            library_service=container.library_service,
            settings=settings,
            gpu_lock=container.gpu_lock,
            visual_analysis_service=container.visual_analysis_service,
            worker_python=tools.python,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
        )

    if ov and ov.video_text_edit_service is not None:
        container.video_text_edit_service = ov.video_text_edit_service
    else:
        from ttvturbo.video_text_edit import VideoTextEditService, VideoTextEditStorage

        container.video_text_edit_service = VideoTextEditService(
            storage=VideoTextEditStorage(paths.video_text_edit),
            library_service=container.library_service,
            settings=settings,
            gpu_lock=container.gpu_lock,
            visual_analysis_service=container.visual_analysis_service,
            worker_python=tools.python,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
        )

    if ov and ov.video_cut_service is not None:
        container.video_cut_service = ov.video_cut_service
    else:
        from ttvturbo.video_cut import VideoCutService, VideoCutStorage

        container.video_cut_service = VideoCutService(
            storage=VideoCutStorage(paths.video_cut),
            library_service=container.library_service,
            settings=settings,
            gpu_lock=container.gpu_lock,
            worker_python=tools.python,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
        )

    if ov and ov.rendering_service is not None:
        container.rendering_service = ov.rendering_service
    else:
        from ttvturbo.rendering import RenderingService, RenderingStorage

        container.rendering_service = RenderingService(
            storage=RenderingStorage(paths.rendering),
            edit_project_service=container.edit_project_service,
            library_service=container.library_service,
            settings=settings,
            worker_python=tools.python,
            ffmpeg_path=tools.ffmpeg,
            ffprobe_path=tools.ffprobe,
        )

    # --- Profile reference resolver --------------------------------------
    def _resolve_profile_reference(profile_id: str, script_id: str) -> dict:
        from ttvturbo.voice_clone.service import ValidationError as _VCValidationError
        from ttvturbo.voice_profiles import (
            VoiceProfileNotFoundError,
            VoiceScriptNotFoundError,
            ReferenceStatus,
        )

        try:
            profile = container.voice_profile_service.get_profile(profile_id)
        except VoiceProfileNotFoundError as exc:
            raise _VCValidationError(f"Unknown voice profile: {profile_id}") from exc
        refs = profile.get("references", {}) or {}
        ref = refs.get(script_id)
        if ref is None:
            raise _VCValidationError(
                f"Profile {profile_id} has no reference for script {script_id}."
            )
        if ref.get("status") != ReferenceStatus.ACCEPTED.value:
            raise _VCValidationError(
                f"Reference for script {script_id} is not ACCEPTED "
                f"(status: {ref.get('status')})."
            )
        return {
            "recording_filename": ref.get("recording_filename"),
            "script_text": ref.get("script_text"),
            "profile_name": profile.get("name"),
        }

    if container.voice_clone_service is not None:
        container.voice_clone_service.set_profile_reference_resolver(
            _resolve_profile_reference
        )


# ---------------------------------------------------------------------------
# App-level router (status, recordings, voice-clone, SPA)
# ---------------------------------------------------------------------------


def build_app_router(container: ServiceContainer) -> Any:
    """Build the router for app-level routes (status, recordings, voice-clone, SPA).

    This is now a thin aggregator that composes the domain-specific
    routers extracted into ``status_api``, ``recordings_api``,
    ``voice_clone_api`` and ``spa_api``.  The SPA catch-all is included
    last so it does not shadow API routes.
    """
    from fastapi import APIRouter

    from ttvturbo.recordings_api import build_recordings_router
    from ttvturbo.spa_api import build_spa_router
    from ttvturbo.status_api import build_status_router
    from ttvturbo.voice_clone_api import build_voice_clone_router

    router = APIRouter(tags=["app"])
    router.include_router(build_status_router(container))
    router.include_router(build_recordings_router(container))
    router.include_router(build_voice_clone_router(container))
    # SPA catch-all must be last so it does not shadow /api/* routes.
    router.include_router(build_spa_router(container))
    return router


# ---------------------------------------------------------------------------
# create_app factory
# ---------------------------------------------------------------------------


def create_app(
    settings: Optional[Settings] = None,
    overrides: Optional[ServiceOverrides] = None,
) -> FastAPI:
    """Create a fully wired FastAPI application.

    Parameters
    ----------
    settings:
        Typed configuration.  Defaults to :meth:`Settings.from_env`.
    overrides:
        Optional service fakes for tests.  When a field is not ``None`` the
        lifespan uses the provided instance instead of constructing a real
        one.

    Importing this function does **not** create directories, recover jobs,
    start workers or load models.  All of that happens inside the lifespan
    when the server (or ``TestClient``) starts.
    """
    if settings is None:
        settings = Settings.from_env()

    container = ServiceContainer()
    container.settings = settings

    # Proxies for lazy service access — route handlers call methods on these
    # which delegate to the real services once the lifespan populates the
    # container.
    library_proxy = _ServiceProxy(container, "library_service")
    voice_clone_proxy = _ServiceProxy(container, "voice_clone_service")
    voice_profile_proxy = _ServiceProxy(container, "voice_profile_service")
    vod_proxy = _ServiceProxy(container, "vod_pipeline_service")
    audio_proxy = _ServiceProxy(container, "audio_extraction_service")
    transcription_proxy = _ServiceProxy(container, "transcription_service")
    pipeline_proxy = _ServiceProxy(container, "pipeline_service")
    mining_proxy = _ServiceProxy(container, "mining_service")
    editor_command_proxy = _ServiceProxy(container, "editor_command_service")
    upload_proxy = _ServiceProxy(container, "upload_storage")
    benchmark_proxy = _ServiceProxy(container, "asr_benchmark_service")
    preset_store_proxy = _ServiceProxy(container, "asr_default_preset_store")
    forensics_proxy = _ServiceProxy(container, "audio_forensics_service")
    visual_analysis_proxy = _ServiceProxy(container, "visual_analysis_service")
    video_generation_proxy = _ServiceProxy(container, "video_generation_service")
    ideas_research_proxy = _ServiceProxy(container, "ideas_research_service")
    editing_proxy = _ServiceProxy(container, "edit_project_service")
    video_upscale_proxy = _ServiceProxy(container, "video_upscale_service")
    video_background_removal_proxy = _ServiceProxy(container, "video_background_removal_service")
    video_text_edit_proxy = _ServiceProxy(container, "video_text_edit_service")
    video_cut_proxy = _ServiceProxy(container, "video_cut_service")
    rendering_proxy = _ServiceProxy(container, "rendering_service")

    quality_analyzer = make_voice_profile_quality_analyzer(voice_clone_proxy)

    voice_profiles_router = build_voice_profiles_router(
        voice_profile_proxy, quality_analyzer=quality_analyzer
    )
    vod_pipeline_router = build_vod_pipeline_router(vod_proxy)
    twitch_status_router = build_twitch_status_router(vod_proxy)
    media_processing_router = build_media_processing_router(
        audio_service=audio_proxy,
        transcription_service=transcription_proxy,
        pipeline_service=pipeline_proxy,
        upload_storage=upload_proxy,
        library_service=library_proxy,
        max_upload_bytes=settings.max_upload_bytes,
    )
    library_router = build_library_router(library_proxy, max_upload_bytes=settings.max_upload_bytes)
    asr_router = build_asr_router(
        benchmark_service=benchmark_proxy,
        default_store=preset_store_proxy,
        forensics_service=forensics_proxy,
    )
    conversation_mining_router = build_conversation_mining_router(mining_proxy)
    editor_command_router = build_editor_command_router(editor_command_proxy)
    visual_analysis_router = build_visual_analysis_router(visual_analysis_proxy)
    video_generation_router = build_video_generation_router(video_generation_proxy)
    ideas_research_router = build_ideas_research_router(ideas_research_proxy)
    editing_router = build_editing_router(editing_proxy)
    video_upscale_router = build_video_upscale_router(video_upscale_proxy)
    video_background_removal_router = build_video_background_removal_router(video_background_removal_proxy)
    video_text_edit_router = build_video_text_edit_router(video_text_edit_proxy)
    video_cut_router = build_video_cut_router(video_cut_proxy)
    rendering_router = build_rendering_router(rendering_proxy)
    app_router = build_app_router(container)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        _init_services(container, settings, overrides)
        container.start_time_monotonic = time.monotonic()
        try:
            yield
        finally:
            # Shutdown all services that own subprocesses/threads, in
            # reverse order of initialisation.  Each shutdown is
            # idempotent and a failure in one does not block the rest.
            from ttvturbo.lifecycle import shutdown_service

            shutdown_service(container.rendering_service)
            shutdown_service(container.video_text_edit_service)
            shutdown_service(container.video_cut_service)
            shutdown_service(container.video_background_removal_service)
            shutdown_service(container.video_upscale_service)
            shutdown_service(container.audio_forensics_service)
            shutdown_service(container.asr_benchmark_service)
            shutdown_service(container.visual_analysis_service)
            shutdown_service(container.video_generation_service)
            shutdown_service(container.ideas_research_service)
            shutdown_service(container.pipeline_service)
            shutdown_service(container.mining_service)
            shutdown_service(container.editor_command_service)
            shutdown_service(container.transcription_service)
            shutdown_service(container.audio_extraction_service)
            shutdown_service(container.vod_pipeline_service)
            shutdown_service(container.voice_clone_service)
            shutdown_service(container.voice_profile_service)

    app = FastAPI(title=APP_NAME, lifespan=_lifespan)
    app.state.container = container
    app.state.settings = settings

    # Store router references in the container so tests can access
    # ``router.state`` to swap services/analyzers when needed.
    container.voice_profiles_router = voice_profiles_router
    container.vod_pipeline_router = vod_pipeline_router
    container.twitch_status_router = twitch_status_router
    container.media_processing_router = media_processing_router
    container.conversation_mining_router = conversation_mining_router
    container.editor_command_router = editor_command_router
    container.library_router = library_router
    container.asr_router = asr_router
    container.visual_analysis_router = visual_analysis_router
    container.video_generation_router = video_generation_router
    container.ideas_research_router = ideas_research_router
    container.editing_router = editing_router
    container.video_upscale_router = video_upscale_router
    container.video_background_removal_router = video_background_removal_router
    container.video_text_edit_router = video_text_edit_router
    container.video_cut_router = video_cut_router
    container.rendering_router = rendering_router
    container.app_router = app_router

    # Register routers.  The app-level router (which includes the SPA
    # catch-all) is registered first so its ``/`` and ``/api/*`` routes are
    # available, but the SPA fallback ``/{full_path:path}`` is registered
    # last because it is a catch-all.  FastAPI matches routes in
    # registration order, so specific ``/api/*`` routes in later routers
    # still take precedence over the catch-all.
    app.include_router(voice_profiles_router)
    app.include_router(vod_pipeline_router)
    app.include_router(twitch_status_router)
    app.include_router(media_processing_router)
    app.include_router(conversation_mining_router)
    app.include_router(editor_command_router)
    app.include_router(library_router)
    app.include_router(asr_router)
    app.include_router(visual_analysis_router)
    app.include_router(video_generation_router)
    app.include_router(ideas_research_router)
    app.include_router(editing_router)
    app.include_router(video_upscale_router)
    app.include_router(video_background_removal_router)
    app.include_router(video_text_edit_router)
    app.include_router(video_cut_router)
    app.include_router(rendering_router)
    # App-level routes (status, recordings, voice-clone, SPA fallback).
    # Registered last so the SPA catch-all does not shadow /api/* routes
    # from the feature routers above.
    app.include_router(app_router)

    return app
