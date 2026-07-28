"""FastAPI application factory with centralized settings and test isolation.

Importing this module has **no side effects** — no directories are created, no
services are constructed, no jobs are recovered.  All of that happens inside
the :func:`create_app` lifespan when the server (or ``TestClient``) starts.

``create_app(settings, overrides)`` returns a fully wired ``FastAPI`` instance.
Route handlers read from a :class:`ServiceContainer` that is populated by the
lifespan, so the app object can be created without touching the filesystem.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ttvturbo.settings import (
    ALLOWED_UPLOAD_EXTENSIONS,
    ALLOWED_UPLOAD_MIME_TYPES,
    APP_NAME,
    APP_VERSION,
    MAX_UPLOAD_BYTES,
    DataPaths,
    Settings,
)

from ttvturbo.voice_clone.schemas import CreateGenerationRequest
from ttvturbo.voice_clone.service import ValidationError as VoiceCloneValidationError
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
    GpuLock,
    MediaJobStorage,
    MediaSourceResolver,
    PipelineService,
    TranscriptionService,
    UploadStorage,
)
from ttvturbo.media_processing_api import build_media_processing_router
from ttvturbo.asr_api import build_asr_router

from ttvturbo.library import LibraryService, LibraryStorage
from ttvturbo.library_api import build_library_router

logger = logging.getLogger("ttvturbo")


# ---------------------------------------------------------------------------
# Executable helper (moved from ttvturbo.app.py so media_processing modules can import
# it without triggering app construction side effects).
# ---------------------------------------------------------------------------


def find_executable(name: str) -> str | None:
    """Find an executable by name.

    Tries PATH first, then falls back to common Windows install locations
    (winget, Program Files, C:\\ffmpeg) so the app still works in shells
    whose PATH was not refreshed after installing FFmpeg.
    """
    found = shutil.which(name)
    if found:
        return found

    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        candidates: list[Path] = []
        if local_app:
            candidates.append(
                Path(local_app) / "Microsoft" / "WinGet" / "Packages"
            )
        candidates += [
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
            Path("C:/ffmpeg"),
            Path("C:/tools/ffmpeg"),
        ]
        for base in candidates:
            if not base.exists():
                continue
            for hit in base.rglob(f"{name}.exe"):
                return str(hit)
    return None


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
        self.asr_benchmark_service: Any = None
        self.audio_forensics_service: Any = None
        # Router references (for tests that need to swap router.state).
        self.voice_profiles_router: Any = None
        self.vod_pipeline_router: Any = None
        self.twitch_status_router: Any = None
        self.media_processing_router: Any = None
        self.library_router: Any = None
        self.asr_router: Any = None
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
    asr_benchmark_service: Any = None
    audio_forensics_service: Any = None


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
        container.library_service = LibraryService(container.library_storage)

    # --- Voice clone ------------------------------------------------------
    if ov and ov.voice_clone_service is not None:
        container.voice_clone_service = ov.voice_clone_service
    else:
        container.voice_clone_service = VoiceCloneService(
            recordings_dir=paths.recordings,
            voice_clones_dir=paths.voice_clones,
            gpu_lock=container.gpu_lock,
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
        )

    # Wire the audio-ready callback after both services exist.
    if container.audio_extraction_service is not None and container.transcription_service is not None:
        container.audio_extraction_service._on_job_ready = (  # noqa: SLF001
            container.transcription_service.on_audio_ready
        )

    if ov and ov.pipeline_service is not None:
        container.pipeline_service = ov.pipeline_service
    else:
        container.pipeline_service = PipelineService(
            storage=container.media_job_storage,
            vod_service=container.vod_pipeline_service,
            audio_service=container.audio_extraction_service,
            transcription_service=container.transcription_service,
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
# App-level router (recordings, voice-clone, status, SPA)
# ---------------------------------------------------------------------------


def _read_wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            if rate <= 0:
                return None
            return frames / float(rate)
    except (wave.Error, EOFError, OSError) as exc:
        logger.warning("Skipping corrupted WAV %s: %s", path.name, exc)
        return None


def _is_temp_or_hidden(name: str) -> bool:
    if not name.lower().endswith(".wav"):
        return True
    if name.startswith(".") or name.startswith("~"):
        return True
    lower = name.lower()
    for suffix in (".tmp", ".part", ".bak", ".swp"):
        if lower.endswith(suffix):
            return True
    return False


def _safe_filename(filename: str) -> str | None:
    if not filename:
        return None
    if "/" in filename or "\\" in filename:
        return None
    if filename.startswith(".") or filename.startswith("~"):
        return None
    safe = Path(filename).name
    if safe != filename:
        return None
    if not safe.lower().endswith(".wav"):
        return None
    return safe


def build_app_router(container: ServiceContainer) -> Any:
    """Build the router for app-level routes (status, recordings, voice-clone, SPA).

    Handlers read from *container* at request time, so the router can be
    registered before the lifespan populates the services.
    """
    from fastapi import APIRouter, Request

    router = APIRouter(tags=["app"])

    # --- helpers that need container state ----------------------------------

    def _recordings_dir() -> Path:
        assert container.paths is not None
        return container.paths.recordings

    def _frontend_dist() -> Path:
        assert container.settings is not None
        return container.settings.frontend_dist

    def _list_recordings() -> list[dict]:
        rec_dir = _recordings_dir()
        items: list[dict] = []
        for entry in rec_dir.iterdir():
            if not entry.is_file():
                continue
            if _is_temp_or_hidden(entry.name):
                continue
            duration = _read_wav_duration(entry)
            if duration is None:
                continue
            stat = entry.stat()
            created_at = _dt.datetime.fromtimestamp(
                stat.st_mtime, tz=_dt.timezone.utc
            ).astimezone().replace(microsecond=0).isoformat()
            items.append({
                "filename": entry.name,
                "created_at": created_at,
                "duration_seconds": round(duration, 2),
                "file_size_bytes": stat.st_size,
                "audio_url": f"/api/recordings/{entry.name}",
            })
        items.sort(key=lambda r: r["created_at"], reverse=True)
        return items

    def _recordings_summary() -> dict:
        recordings = _list_recordings()
        total_duration = sum(r["duration_seconds"] for r in recordings)
        total_size = sum(r["file_size_bytes"] for r in recordings)
        return {
            "count": len(recordings),
            "total_duration_seconds": round(total_duration, 2),
            "total_size_bytes": total_size,
        }

    def _free_storage_bytes() -> int:
        try:
            usage = shutil.disk_usage(_recordings_dir())
            return int(usage.free)
        except OSError as exc:
            logger.warning("Could not determine free disk space: %s", exc)
            return 0

    def _spa_index() -> FileResponse:
        index_html = _frontend_dist() / "index.html"
        if not index_html.is_file():
            raise HTTPException(
                status_code=404,
                detail="frontend not built. Run `npm --prefix frontend run build`.",
            )
        return FileResponse(index_html, media_type="text/html")

    # --- SPA root -----------------------------------------------------------

    @router.get("/")
    def index() -> FileResponse:
        if (_frontend_dist() / "index.html").is_file():
            return _spa_index()
        raise HTTPException(
            status_code=404,
            detail="frontend/dist/index.html not found - build the React frontend first",
        )

    # --- status -------------------------------------------------------------

    @router.get("/api/status")
    def get_status() -> JSONResponse:
        ffmpeg = find_executable("ffmpeg")
        vc_status = container.voice_clone_service.status()
        vp_count = 0
        vp_clone_ready = 0
        vp_complete = 0
        vp_available = "available"
        try:
            profiles = container.voice_profile_service.list_profiles()
            vp_count = len(profiles)
            for p in profiles:
                progress = p.get("progress") or {}
                if progress.get("clone_ready"):
                    vp_clone_ready += 1
                if progress.get("pack_complete"):
                    vp_complete += 1
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("voice-profile status aggregation failed: %s", exc)
            vp_available = "unavailable"
        vod_pipeline_agg = {
            "profiles": 0,
            "vods": 0,
            "ready": 0,
            "active": 0,
            "failed": 0,
            "downloaded_bytes": 0,
        }
        vod_pipeline_available = "available"
        try:
            vod_pipeline_agg = container.vod_pipeline_service.aggregate_status()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("vod-pipeline status aggregation failed: %s", exc)
            vod_pipeline_available = "unavailable"
        audio_agg = {"total": 0, "ready": 0, "failed": 0, "active": 0}
        transcription_agg = {"total": 0, "ready": 0, "failed": 0, "active": 0}
        pipeline_agg = {"total": 0, "active": 0, "ready_for_clip_analysis": 0, "failed": 0}
        transcription_available = "available"
        audio_available = "available"
        vod_pipeline_orch_available = "available"
        clip_finder_available = "not_implemented"
        try:
            audio_agg = container.audio_extraction_service.aggregate_status()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("audio-extraction status aggregation failed: %s", exc)
            audio_available = "unavailable"
        try:
            transcription_agg = container.transcription_service.aggregate_status()
            rt = container.transcription_service.runtime_status()
            if not rt.get("available") and not rt.get("busy"):
                transcription_available = "unavailable"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("transcription status aggregation failed: %s", exc)
            transcription_available = "unavailable"
        try:
            pipeline_agg = container.pipeline_service.aggregate_status()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("pipeline status aggregation failed: %s", exc)
            vod_pipeline_orch_available = "unavailable"
        audio_artifacts_count = 0
        try:
            vods_root = container.vod_pipeline_service.storage.vods_dir
            if vods_root.is_dir():
                for vod_dir in vods_root.iterdir():
                    if not vod_dir.is_dir():
                        continue
                    meta = vod_dir / "artifacts" / "audio" / "metadata.json"
                    if meta.is_file():
                        audio_artifacts_count += 1
        except Exception:  # pragma: no cover - defensive
            pass
        transcripts_count = 0
        try:
            transcripts_count = len(container.transcription_service.list_transcriptions())
        except Exception:  # pragma: no cover - defensive
            pass
        return JSONResponse(content={
            "status": "online",
            "app_name": APP_NAME,
            "version": APP_VERSION,
            "uptime_seconds": round(time.monotonic() - container.start_time_monotonic, 1),
            "recordings": _recordings_summary(),
            "storage": {
                "free_bytes": _free_storage_bytes(),
            },
            "features": {
                "recording": "available" if ffmpeg is not None else "unavailable",
                "voice_cloning": "available" if vc_status["available"] else "unavailable",
                "voice_profiles": vp_available,
                "twitch_profiles": vod_pipeline_available,
                "vod_downloader": vod_pipeline_available,
                "vod_pipeline": vod_pipeline_orch_available,
                "audio_extraction": audio_available,
                "transcription": transcription_available,
                "clip_finder": clip_finder_available,
                "vod_analysis": "not_implemented",
                "video_editor": "not_implemented",
            },
            "voice_clone_runtime": {
                "available": vc_status["available"],
                "device": vc_status["device"],
                "torch_version": vc_status["torch_version"],
                "torch_cuda_version": vc_status["torch_cuda_version"],
                "cuda_available": vc_status["cuda_available"],
                "device_name": vc_status["device_name"],
                "vram_total_bytes": vc_status["vram_total_bytes"],
                "vram_free_bytes": vc_status["vram_free_bytes"],
                "qwen_tts_importable": vc_status["qwen_tts_importable"],
                "reasons": vc_status["reasons"],
                "warnings": vc_status["warnings"],
            },
            "voice_profiles": {
                "count": vp_count,
                "clone_ready_count": vp_clone_ready,
                "complete_count": vp_complete,
            },
            "vod_pipeline": vod_pipeline_agg,
            "media_processing": {
                "audio_artifacts": audio_artifacts_count,
                "transcripts": transcripts_count,
                "audio_jobs": audio_agg,
                "transcription_jobs": transcription_agg,
                "pipeline_runs": pipeline_agg,
            },
        })

    # --- recordings ---------------------------------------------------------

    @router.post("/api/recordings")
    async def upload_recording(audio: UploadFile = File(...)) -> JSONResponse:
        ffmpeg_path = find_executable("ffmpeg")
        if ffmpeg_path is None:
            raise HTTPException(
                status_code=500,
                detail="ffmpeg not found on server PATH. Install FFmpeg to convert recordings.",
            )

        if not audio.filename:
            raise HTTPException(status_code=400, detail="No filename provided.")

        suffix = Path(audio.filename).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file extension: {suffix or '(none)'}.",
            )
        content_type = (audio.content_type or "").lower().split(";", 1)[0].strip()
        if content_type and content_type not in ALLOWED_UPLOAD_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported content type: {content_type}.",
            )

        recording_id = uuid.uuid4().hex
        suffix = suffix or ".webm"
        tmp_in = Path(tempfile.gettempdir()) / f"ttvturbo_{recording_id}{suffix}"
        wav_name = f"{recording_id}.wav"
        wav_path = _recordings_dir() / wav_name

        try:
            total = 0
            with tmp_in.open("wb") as fh:
                while True:
                    chunk = await audio.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        fh.close()
                        tmp_in.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=f"Upload exceeds {MAX_UPLOAD_BYTES} bytes.",
                        )
                    fh.write(chunk)
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - disk error path
            tmp_in.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Failed to store upload: {exc}") from exc

        if tmp_in.stat().st_size == 0:
            tmp_in.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        cmd = [
            ffmpeg_path,
            "-y",
            "-i", str(tmp_in),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "1",
            str(wav_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            tmp_in.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"ffmpeg not executable: {exc}") from exc

        if proc.returncode != 0:
            tmp_in.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            stderr = proc.stderr.decode("utf-8", errors="replace")[-2000:]
            raise HTTPException(status_code=500, detail=f"ffmpeg conversion failed: {stderr}")

        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            tmp_in.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="WAV file missing or empty after conversion.")

        ffprobe = find_executable("ffprobe") or ffmpeg_path
        probe = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,channels,sample_rate",
                "-of", "default=noprint_wrappers=1",
                str(wav_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if probe.returncode != 0:
            tmp_in.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            stderr = probe.stderr.decode("utf-8", errors="replace")[-2000:]
            raise HTTPException(status_code=500, detail=f"WAV not playable: {stderr}")

        tmp_in.unlink(missing_ok=True)

        return JSONResponse(
            status_code=201,
            content={
                "filename": wav_name,
                "url": f"/api/recordings/{wav_name}",
                "size_bytes": wav_path.stat().st_size,
                "probe": probe.stdout.decode("utf-8", errors="replace").strip(),
            },
        )

    @router.get("/api/recordings")
    def list_recordings() -> JSONResponse:
        return JSONResponse(content={"recordings": _list_recordings()})

    @router.get("/api/recordings/{filename}")
    def get_recording(filename: str) -> FileResponse:
        safe = Path(filename).name
        if safe != filename:
            raise HTTPException(status_code=400, detail="Invalid filename.")
        if not safe.lower().endswith(".wav"):
            raise HTTPException(status_code=400, detail="Invalid filename.")
        wav_path = _recordings_dir() / safe
        if not wav_path.is_file():
            raise HTTPException(status_code=404, detail="Recording not found.")
        return FileResponse(wav_path, media_type="audio/wav", filename=safe)

    @router.delete("/api/recordings/{filename}")
    def delete_recording(filename: str) -> JSONResponse:
        safe = _safe_filename(filename)
        if safe is None:
            raise HTTPException(status_code=400, detail="Invalid filename.")
        wav_path = _recordings_dir() / safe
        if not wav_path.is_file():
            raise HTTPException(status_code=404, detail="Recording not found.")
        try:
            using_profiles = container.voice_profile_service.find_profiles_using_recording(safe)
        except Exception:  # pragma: no cover - defensive
            using_profiles = []
        if using_profiles:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "recording_in_use",
                        "message": "Die Aufnahme wird von einem Voice Profile verwendet.",
                        "profiles": [
                            {"id": p.get("id"), "name": p.get("name")}
                            for p in using_profiles
                        ],
                    }
                },
            )
        try:
            wav_path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not delete file: {exc}") from exc
        return JSONResponse(content={"filename": safe, "deleted": True})

    # --- voice clone --------------------------------------------------------

    @router.get("/api/voice-clone/status")
    def voice_clone_status() -> JSONResponse:
        return JSONResponse(content=container.voice_clone_service.status())

    @router.post("/api/voice-clone/preload-model")
    async def voice_clone_preload_model() -> JSONResponse:
        import asyncio
        from functools import partial

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, partial(container.voice_clone_service.preload_model)
        )
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error", "model preload failed"))
        return JSONResponse(content=result)

    @router.get("/api/voice-clone/analyze-reference/{filename}")
    def voice_clone_analyze_reference(filename: str) -> JSONResponse:
        try:
            result = container.voice_clone_service.analyze_reference(filename)
        except VoiceCloneValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(content=result)

    @router.post("/api/voice-clone/generations")
    def create_voice_clone_generation(request: CreateGenerationRequest) -> JSONResponse:
        try:
            meta = container.voice_clone_service.create_generation(request.model_dump())
        except VoiceCloneValidationError as exc:
            if "already running" in str(exc):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(status_code=201, content={"id": meta["id"], "status": meta["status"]})

    @router.get("/api/voice-clone/generations")
    def list_voice_clone_generations() -> JSONResponse:
        return JSONResponse(content={"generations": container.voice_clone_service.list_generations()})

    @router.get("/api/voice-clone/generations/{generation_id}")
    def get_voice_clone_generation(generation_id: str) -> JSONResponse:
        try:
            meta = container.voice_clone_service.get_generation(generation_id)
        except VoiceCloneValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if meta is None:
            raise HTTPException(status_code=404, detail="Generation not found.")
        return JSONResponse(content=meta)

    @router.get("/api/voice-clone/generations/{generation_id}/audio")
    def get_voice_clone_audio(generation_id: str) -> FileResponse:
        try:
            out = container.voice_clone_service.output_path_for(generation_id)
        except VoiceCloneValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if out is None:
            raise HTTPException(status_code=404, detail="Audio not available for this generation.")
        return FileResponse(out, media_type="audio/wav", filename="output.wav")

    @router.delete("/api/voice-clone/generations/{generation_id}")
    def delete_voice_clone_generation(generation_id: str) -> JSONResponse:
        try:
            deleted = container.voice_clone_service.delete_generation(generation_id)
        except VoiceCloneValidationError as exc:
            if "currently running" in str(exc):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Generation not found.")
        return JSONResponse(content={"id": generation_id, "deleted": True})

    @router.get("/api/voice-clone/generations/{generation_id}/log")
    def get_voice_clone_log(generation_id: str) -> JSONResponse:
        try:
            excerpt = container.voice_clone_service.worker_log_excerpt(generation_id)
        except VoiceCloneValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if excerpt is None:
            raise HTTPException(status_code=404, detail="No worker log for this generation.")
        return JSONResponse(content={"id": generation_id, "log": excerpt})

    # --- SPA fallback (must be registered AFTER all API routes) -------------

    @router.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")

        dist = _frontend_dist()
        if full_path:
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="Not found.")
            if candidate.is_file():
                return FileResponse(candidate)

        return _spa_index()

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
    upload_proxy = _ServiceProxy(container, "upload_storage")
    benchmark_proxy = _ServiceProxy(container, "asr_benchmark_service")
    preset_store_proxy = _ServiceProxy(container, "asr_default_preset_store")
    forensics_proxy = _ServiceProxy(container, "audio_forensics_service")

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
    )
    library_router = build_library_router(library_proxy)
    asr_router = build_asr_router(
        benchmark_service=benchmark_proxy,
        default_store=preset_store_proxy,
        forensics_service=forensics_proxy,
    )
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

            shutdown_service(container.audio_forensics_service)
            shutdown_service(container.asr_benchmark_service)
            shutdown_service(container.pipeline_service)
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
    container.library_router = library_router
    container.asr_router = asr_router
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
    app.include_router(library_router)
    app.include_router(asr_router)
    # App-level routes (status, recordings, voice-clone, SPA fallback).
    # Registered last so the SPA catch-all does not shadow /api/* routes
    # from the feature routers above.
    app.include_router(app_router)

    return app
