"""TTVturbo local dashboard backend.

Provides:
  * a real microphone recording pipeline (browser -> FFmpeg -> WAV),
  * a recordings library (list / play / download / delete),
  * a real `/api/status` endpoint with computed system values,
  * serving of the built React frontend (frontend/dist) including
    SPA fallback for unknown non-API routes.

Endpoints:
  GET  /                          -> SPA index (frontend/dist/index.html)
  GET  /api/status                -> real system + recordings status
  POST /api/recordings            -> receives a browser recording, converts to WAV
  GET  /api/recordings            -> lists all stored WAV recordings (newest first)
  GET  /api/recordings/{filename} -> streams a stored WAV file
  DELETE /api/recordings/{filename} -> deletes a stored WAV file
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from voice_clone.schemas import (
    CreateGenerationRequest,
    GenerationStatus,
)
from voice_clone.service import ValidationError as VoiceCloneValidationError
from voice_clone.service import VoiceCloneService

from voice_profiles_api import (
    build_router as build_voice_profiles_router,
    build_service as build_voice_profile_service,
    make_quality_analyzer as make_voice_profile_quality_analyzer,
)

from vod_pipeline_api import (
    build_router as build_vod_pipeline_router,
    build_service as build_vod_pipeline_service,
    build_twitch_status_router as build_twitch_status_router,
)

from media_processing import (
    AudioExtractionService,
    GpuLock,
    MediaJobStorage,
    MediaSourceResolver,
    PipelineService,
    TranscriptionService,
    UploadStorage,
)
from media_processing_api import build_media_processing_router
from media_processing import AsrBenchmarkService, AsrDefaultPresetStore
from asr_api import build_asr_router

from library import LibraryService, LibraryStorage
from library_api import build_library_router

logger = logging.getLogger("ttvturbo")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"

# Single runtime data root. Every persistent artifact lives under this
# directory so runtime state has one home on disk::
#
#     data/
#       recordings/        <- browser recordings (WAV)
#       voice_clones/      <- voice-clone generations
#       voice_profiles/    <- voice-profile JSON records
#       twitch_profiles/   <- VOD-pipeline Twitch profiles
#       vods/              <- VOD metadata + temp download files (session data)
#       library/           <- persistent video store (downloaded VODs + uploads)
#       media_jobs/        <- media-processing jobs
#       pipeline_runs/     <- pipeline run records
#
# Configurable via the TTVTURBO_DATA_DIR environment variable. The whole
# directory is git-ignored (see .gitignore).
DATA_DIR = Path(
    os.environ.get("TTVTURBO_DATA_DIR") or (BASE_DIR / "data")
)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Library — the persistent, independent video store. Downloaded VOD files
# and manual uploads live here. Survives profile/VOD deletion.
TTVTURBO_LIBRARY_DIR = Path(
    os.environ.get("TTVTURBO_LIBRARY_DIR") or (DATA_DIR / "library")
)
library_storage = LibraryStorage(TTVTURBO_LIBRARY_DIR)
library_service = LibraryService(library_storage)

RECORDINGS_DIR = DATA_DIR / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

# Voice-clone vertical slice. The service owns persistence, validation and
# the single Qwen3-TTS worker subprocess. It recovers persisted state on
# startup so generations survive a server restart.
VOICE_CLONES_DIR = DATA_DIR / "voice_clones"

# Project-wide cross-process GPU lock, shared between Qwen3-TTS
# voice-clone and faster-whisper transcription. Lives on disk so two
# separate Python processes see the same owner. Reaped on startup.
gpu_lock = GpuLock(DATA_DIR)

voice_clone_service = VoiceCloneService(
    recordings_dir=RECORDINGS_DIR,
    voice_clones_dir=VOICE_CLONES_DIR,
    gpu_lock=gpu_lock,
)

# Voice-profile integration. Exactly one library, storage and service
# instance is built at startup. The persistence directory is configurable
# via TTVTURBO_VOICE_PROFILES_DIR and defaults to data/voice_profiles/
# under the shared data root. The real voice-clone quality analyzer is
# delegated to the voice-profile API so reference quality is always
# computed server-side.
VOICE_PROFILES_DIR = Path(
    os.environ.get("TTVTURBO_VOICE_PROFILES_DIR") or (DATA_DIR / "voice_profiles")
)
VOICE_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
voice_profile_service = build_voice_profile_service(
    recordings_dir=RECORDINGS_DIR,
    voice_profiles_dir=VOICE_PROFILES_DIR,
)
_voice_profile_quality_analyzer = make_voice_profile_quality_analyzer(voice_clone_service)
voice_profiles_router = build_voice_profiles_router(
    voice_profile_service, quality_analyzer=_voice_profile_quality_analyzer
)

# VOD pipeline (Phase 1). File-based persistence under the data dir; the
# download worker runs in a separate Python process so FastAPI stays
# responsive during multi-hour downloads. Twitch credentials are read
# from the environment and never persisted or logged.
TTVTURBO_VOD_DOWNLOAD_DIR = Path(
    os.environ.get("TTVTURBO_VOD_DOWNLOAD_DIR") or (DATA_DIR / "vods")
)
vod_pipeline_service = build_vod_pipeline_service(
    data_dir=DATA_DIR,
    download_dir=TTVTURBO_VOD_DOWNLOAD_DIR,
    library_service=library_service,
)
vod_pipeline_router = build_vod_pipeline_router(vod_pipeline_service)
twitch_status_router = build_twitch_status_router(vod_pipeline_service)

# Shared media-processing services: audio extraction, transcription and
# the VOD pipeline orchestration. These reuse the same VOD storage and
# download service above — the pipeline never re-implements download,
# audio or transcription logic. The GPU lock is the same instance shared
# with voice-clone so the two GPU workloads never load models
# simultaneously.
media_job_storage = MediaJobStorage(DATA_DIR)
# Upload storage for independent transcription (not tied to VOD downloader).
TTVTURBO_UPLOADS_DIR = Path(
    os.environ.get("TTVTURBO_UPLOADS_DIR") or (DATA_DIR / "uploads")
)
upload_storage = UploadStorage(TTVTURBO_UPLOADS_DIR)
media_source_resolver = MediaSourceResolver(
    vod_pipeline_service.storage,
    upload_storage=upload_storage,
    library_service=library_service,
)
audio_extraction_service = AudioExtractionService(
    storage=media_job_storage,
    source_resolver=media_source_resolver,
)
transcription_service = TranscriptionService(
    storage=media_job_storage,
    source_resolver=media_source_resolver,
    audio_service=audio_extraction_service,
    gpu_lock=gpu_lock,
)
# Wire the audio-ready callback after both services exist so dependent
# TRANSCRIBE jobs are started immediately when audio extraction completes.
# This complements TranscriptionService.poll_dependencies() which is called
# by the API layer on each request as a safety net.
audio_extraction_service._on_job_ready = transcription_service.on_audio_ready  # noqa: SLF001
pipeline_service = PipelineService(
    storage=media_job_storage,
    vod_service=vod_pipeline_service,
    audio_service=audio_extraction_service,
    transcription_service=transcription_service,
)
media_processing_router = build_media_processing_router(
    audio_service=audio_extraction_service,
    transcription_service=transcription_service,
    pipeline_service=pipeline_service,
    upload_storage=upload_storage,
    library_service=library_service,
)
library_router = build_library_router(library_service)

# ASR preset + benchmark system. Shares the same GPU lock and data root
# as the transcription service. The default-preset store persists the
# production default selection under the data directory.
asr_default_preset_store = AsrDefaultPresetStore(DATA_DIR)
asr_benchmark_service = AsrBenchmarkService(
    data_dir=DATA_DIR,
    source_resolver=media_source_resolver,
    gpu_lock=gpu_lock,
)
asr_router = build_asr_router(
    benchmark_service=asr_benchmark_service,
    default_store=asr_default_preset_store,
)

# Connect the voice-clone profile mode to the voice-profile service. The
# resolver runs server-side: it loads the profile, finds the accepted
# reference for the given script id, and returns the real WAV filename and
# the stored script text. The client can never supply either value in
# profile mode.
def _resolve_profile_reference(profile_id: str, script_id: str) -> dict:
    from voice_clone.service import ValidationError as _VCValidationError
    from voice_profiles import (
        VoiceProfileNotFoundError,
        VoiceScriptNotFoundError,
        ReferenceStatus,
    )

    try:
        profile = voice_profile_service.get_profile(profile_id)
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


voice_clone_service.set_profile_reference_resolver(_resolve_profile_reference)

APP_NAME = "TTVturbo"
APP_VERSION = "0.1.0"
START_TIME_MONOTONIC = time.monotonic()

# Upload guardrails.
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

app = FastAPI(title=APP_NAME)

# Voice-profile API routes. Registered before the SPA fallback so /api/*
# routes take precedence over the catch-all.
app.include_router(voice_profiles_router)

# VOD-pipeline API routes (Twitch profiles, VODs) and the Twitch runtime
# diagnostic. Also registered before the SPA fallback.
app.include_router(vod_pipeline_router)
app.include_router(twitch_status_router)

# Media-processing API routes (transcription, audio artifacts, pipeline
# runs). Registered before the SPA fallback so /api/* takes precedence.
app.include_router(media_processing_router)

# Library API routes (persistent video store: items, uploads, file serving).
app.include_router(library_router)

# ASR preset + benchmark API routes (multilingual presets, benchmark
# creation/execution/cancel/delete, default-preset selection).
app.include_router(asr_router)


# --------------------------------------------------------------------------- #
# FFmpeg helpers
# --------------------------------------------------------------------------- #

def _find_executable(name: str) -> str | None:
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
        candidates = []
        if local_app:
            candidates.append(
                Path(local_app)
                / "Microsoft"
                / "WinGet"
                / "Packages"
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


def _ffmpeg_available() -> str | None:
    """Return the ffmpeg executable path or None if not found."""
    return _find_executable("ffmpeg")


# --------------------------------------------------------------------------- #
# WAV / recordings helpers
# --------------------------------------------------------------------------- #

def _read_wav_duration(path: Path) -> float | None:
    """Return the WAV duration in seconds, read from the real file.

    Returns None if the file is not a valid, readable WAV.
    """
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
    """Filter out temp/partial/hidden files that should not be listed."""
    if not name.lower().endswith(".wav"):
        return True
    if name.startswith(".") or name.startswith("~"):
        return True
    lower = name.lower()
    for suffix in (".tmp", ".part", ".bak", ".swp"):
        if lower.endswith(suffix):
            return True
    return False


def _list_recordings() -> list[dict]:
    """Scan RECORDINGS_DIR and return metadata for all valid WAVs, newest first."""
    items: list[dict] = []
    for entry in RECORDINGS_DIR.iterdir():
        if not entry.is_file():
            continue
        if _is_temp_or_hidden(entry.name):
            continue
        duration = _read_wav_duration(entry)
        if duration is None:
            continue  # corrupted or unreadable WAV -> logged, skipped
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
    """Compute real aggregate values over the recordings directory."""
    recordings = _list_recordings()
    total_duration = sum(r["duration_seconds"] for r in recordings)
    total_size = sum(r["file_size_bytes"] for r in recordings)
    return {
        "count": len(recordings),
        "total_duration_seconds": round(total_duration, 2),
        "total_size_bytes": total_size,
    }


def _free_storage_bytes() -> int:
    """Return real free disk space for the recordings partition."""
    try:
        usage = shutil.disk_usage(RECORDINGS_DIR)
        return int(usage.free)
    except OSError as exc:
        logger.warning("Could not determine free disk space: %s", exc)
        return 0


def _safe_filename(filename: str) -> str | None:
    """Return a plain filename if safe, else None.

    Blocks path traversal, absolute paths, hidden/temp files.
    """
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


# --------------------------------------------------------------------------- #
# SPA serving
# --------------------------------------------------------------------------- #

def _spa_index() -> FileResponse:
    index_html = FRONTEND_DIST_DIR / "index.html"
    if not index_html.is_file():
        raise HTTPException(
            status_code=404,
            detail="frontend not built. Run `npm --prefix frontend run build`.",
        )
    return FileResponse(index_html, media_type="text/html")


@app.get("/")
def index() -> FileResponse:
    """Serve the SPA entry point from frontend/dist."""
    if (FRONTEND_DIST_DIR / "index.html").is_file():
        return _spa_index()
    raise HTTPException(
        status_code=404,
        detail="frontend/dist/index.html not found - build the React frontend first",
    )


# --------------------------------------------------------------------------- #
# API: status
# --------------------------------------------------------------------------- #

@app.get("/api/status")
def get_status() -> JSONResponse:
    """Return real, computed system and recordings status."""
    ffmpeg = _ffmpeg_available()
    vc_status = voice_clone_service.status()
    # Real voice-profile aggregate counts. Computed from the actual profile
    # store; never fabricated. Failures degrade to zeros so the rest of the
    # status payload stays intact.
    vp_count = 0
    vp_clone_ready = 0
    vp_complete = 0
    vp_available = "available"
    try:
        profiles = voice_profile_service.list_profiles()
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
    # Real VOD-pipeline aggregate counts. Computed from the actual stores;
    # never fabricated. Failures degrade to zeros so the rest of the
    # status payload stays intact.
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
        vod_pipeline_agg = vod_pipeline_service.aggregate_status()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("vod-pipeline status aggregation failed: %s", exc)
        vod_pipeline_available = "unavailable"
    # Media-processing aggregates (audio artifacts, transcriptions,
    # pipeline runs). Computed from the actual stores; never fabricated.
    audio_agg = {"total": 0, "ready": 0, "failed": 0, "active": 0}
    transcription_agg = {"total": 0, "ready": 0, "failed": 0, "active": 0}
    pipeline_agg = {"total": 0, "active": 0, "ready_for_clip_analysis": 0, "failed": 0}
    transcription_available = "available"
    audio_available = "available"
    vod_pipeline_orch_available = "available"
    clip_finder_available = "not_implemented"
    try:
        audio_agg = audio_extraction_service.aggregate_status()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("audio-extraction status aggregation failed: %s", exc)
        audio_available = "unavailable"
    try:
        transcription_agg = transcription_service.aggregate_status()
        # Transcription feature availability depends on the runtime probe.
        rt = transcription_service.runtime_status()
        if not rt.get("available") and not rt.get("busy"):
            transcription_available = "unavailable"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("transcription status aggregation failed: %s", exc)
        transcription_available = "unavailable"
    try:
        pipeline_agg = pipeline_service.aggregate_status()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("pipeline status aggregation failed: %s", exc)
        vod_pipeline_orch_available = "unavailable"
    # Count audio artifacts by scanning VOD dirs for the artifact metadata.
    audio_artifacts_count = 0
    try:
        vods_root = vod_pipeline_service.storage.vods_dir
        if vods_root.is_dir():
            for vod_dir in vods_root.iterdir():
                if not vod_dir.is_dir():
                    continue
                meta = vod_dir / "artifacts" / "audio" / "metadata.json"
                if meta.is_file():
                    audio_artifacts_count += 1
    except Exception:  # pragma: no cover - defensive
        pass
    # Count transcriptions by scanning transcript metadata files.
    transcripts_count = 0
    try:
        transcripts_count = len(transcription_service.list_transcriptions())
    except Exception:  # pragma: no cover - defensive
        pass
    return JSONResponse(content={
        "status": "online",
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "uptime_seconds": round(time.monotonic() - START_TIME_MONOTONIC, 1),
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
        # Additive voice-clone runtime diagnostics. The frontend ignores
        # unknown keys; these fields let the dashboard show the real GPU
        # availability instead of a hard-coded value.
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
        # Additive voice-profile aggregate status. The frontend ignores
        # unknown keys; no local storage paths are leaked here.
        "voice_profiles": {
            "count": vp_count,
            "clone_ready_count": vp_clone_ready,
            "complete_count": vp_complete,
        },
        # Additive VOD-pipeline aggregate status. The frontend ignores
        # unknown keys; no local storage paths are leaked here.
        "vod_pipeline": vod_pipeline_agg,
        # Additive media-processing aggregates. The frontend ignores
        # unknown keys; no local storage paths are leaked here.
        "media_processing": {
            "audio_artifacts": audio_artifacts_count,
            "transcripts": transcripts_count,
            "audio_jobs": audio_agg,
            "transcription_jobs": transcription_agg,
            "pipeline_runs": pipeline_agg,
        },
    })


# --------------------------------------------------------------------------- #
# API: recordings
# --------------------------------------------------------------------------- #

@app.post("/api/recordings")
async def upload_recording(audio: UploadFile = File(...)) -> JSONResponse:
    ffmpeg_path = _ffmpeg_available()
    if ffmpeg_path is None:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg not found on server PATH. Install FFmpeg to convert recordings.",
        )

    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    # Validate extension and content type before touching disk.
    suffix = Path(audio.filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file extension: {suffix or '(none)'}.",
        )
    content_type = (audio.content_type or "").lower().split(";", 1)[0].strip()
    # Allow through if the type is missing/unknown but the extension is allowed.
    if content_type and content_type not in ALLOWED_UPLOAD_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported content type: {content_type}.",
        )

    recording_id = uuid.uuid4().hex
    suffix = suffix or ".webm"
    tmp_in = Path(tempfile.gettempdir()) / f"ttvturbo_{recording_id}{suffix}"
    wav_name = f"{recording_id}.wav"
    wav_path = RECORDINGS_DIR / wav_name

    try:
        total = 0
        with tmp_in.open("wb") as fh:
            while True:
                chunk = await audio.read(1 << 20)  # 1 MiB
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

    # Convert to a real, playable WAV (PCM 16-bit little-endian).
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

    # Verify the WAV exists, is non-empty, and is readable via ffprobe.
    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        tmp_in.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="WAV file missing or empty after conversion.")

    ffprobe = _find_executable("ffprobe") or ffmpeg_path
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


@app.get("/api/recordings")
def list_recordings() -> JSONResponse:
    return JSONResponse(content={"recordings": _list_recordings()})


@app.get("/api/recordings/{filename}")
def get_recording(filename: str) -> FileResponse:
    # Reject path traversal attempts.
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    if not safe.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    wav_path = RECORDINGS_DIR / safe
    if not wav_path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    return FileResponse(wav_path, media_type="audio/wav", filename=safe)


@app.delete("/api/recordings/{filename}")
def delete_recording(filename: str) -> JSONResponse:
    safe = _safe_filename(filename)
    if safe is None:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    wav_path = RECORDINGS_DIR / safe
    if not wav_path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    # Block deletion while the recording is referenced by any voice profile.
    # The user must detach or replace the reference first.
    try:
        using_profiles = voice_profile_service.find_profiles_using_recording(safe)
    except Exception:  # pragma: no cover - defensive, never block deletion silently
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


# --------------------------------------------------------------------------- #
# API: voice clone
# --------------------------------------------------------------------------- #

@app.get("/api/voice-clone/status")
def voice_clone_status() -> JSONResponse:
    return JSONResponse(content=voice_clone_service.status())


@app.post("/api/voice-clone/preload-model")
async def voice_clone_preload_model() -> JSONResponse:
    """Pre-download the configured Qwen3-TTS model into the HF cache.

    Runs the (potentially long) download in a worker thread so the event
    loop stays responsive. Returns once the download finishes.
    """
    import asyncio
    from functools import partial

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial(voice_clone_service.preload_model))
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "model preload failed"))
    return JSONResponse(content=result)


@app.get("/api/voice-clone/analyze-reference/{filename}")
def voice_clone_analyze_reference(filename: str) -> JSONResponse:
    """Run the technical quality analysis on a recording and return the result.

    Used by the Voice Clone form so the user sees the quality class and
    warnings before starting a generation.
    """
    try:
        result = voice_clone_service.analyze_reference(filename)
    except VoiceCloneValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content=result)


@app.post("/api/voice-clone/generations")
def create_voice_clone_generation(request: CreateGenerationRequest) -> JSONResponse:
    try:
        meta = voice_clone_service.create_generation(request.model_dump())
    except VoiceCloneValidationError as exc:
        # Distinguish "busy" (409) from other validation failures (400).
        if "already running" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(status_code=201, content={"id": meta["id"], "status": meta["status"]})


@app.get("/api/voice-clone/generations")
def list_voice_clone_generations() -> JSONResponse:
    return JSONResponse(content={"generations": voice_clone_service.list_generations()})


@app.get("/api/voice-clone/generations/{generation_id}")
def get_voice_clone_generation(generation_id: str) -> JSONResponse:
    try:
        meta = voice_clone_service.get_generation(generation_id)
    except VoiceCloneValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail="Generation not found.")
    return JSONResponse(content=meta)


@app.get("/api/voice-clone/generations/{generation_id}/audio")
def get_voice_clone_audio(generation_id: str) -> FileResponse:
    try:
        out = voice_clone_service.output_path_for(generation_id)
    except VoiceCloneValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if out is None:
        raise HTTPException(status_code=404, detail="Audio not available for this generation.")
    return FileResponse(out, media_type="audio/wav", filename="output.wav")


@app.delete("/api/voice-clone/generations/{generation_id}")
def delete_voice_clone_generation(generation_id: str) -> JSONResponse:
    try:
        deleted = voice_clone_service.delete_generation(generation_id)
    except VoiceCloneValidationError as exc:
        if "currently running" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Generation not found.")
    return JSONResponse(content={"id": generation_id, "deleted": True})


@app.get("/api/voice-clone/generations/{generation_id}/log")
def get_voice_clone_log(generation_id: str) -> JSONResponse:
    """Return a short, sanitized tail of the worker log.

    The full log stays on disk; the API only ever returns a bounded
    excerpt so a runaway worker cannot exhaust memory. Absolute paths
    are scrubbed.
    """
    try:
        excerpt = voice_clone_service.worker_log_excerpt(generation_id)
    except VoiceCloneValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if excerpt is None:
        raise HTTPException(status_code=404, detail="No worker log for this generation.")
    return JSONResponse(content={"id": generation_id, "log": excerpt})


# --------------------------------------------------------------------------- #
# SPA fallback (must be registered AFTER all API routes).
# --------------------------------------------------------------------------- #

@app.api_route(
    "/{full_path:path}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
def spa_fallback(full_path: str) -> FileResponse:
    """Serve the React SPA for any unknown non-API route.

    Rules:
      * `/api/*` is never handled here (returns 404 JSON, no SPA fallback).
      * Real files inside `frontend/dist` (JS/CSS/assets) are served directly.
      * Everything else falls back to `index.html` so client-side routing
        works on direct visits and reloads.
    """
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found.")

    # Serve a real built asset if it exists.
    if full_path:
        candidate = (FRONTEND_DIST_DIR / full_path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIST_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found.")
        if candidate.is_file():
            return FileResponse(candidate)

    return _spa_index()


def _frontend_needs_build() -> bool:
    """True if frontend/dist is missing or stale relative to frontend/src."""
    index_html = FRONTEND_DIST_DIR / "index.html"
    if not index_html.is_file():
        return True
    src_dir = BASE_DIR / "frontend" / "src"
    if not src_dir.is_dir():
        return False
    built_mtime = index_html.stat().st_mtime
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            if name.endswith((".ts", ".tsx", ".js", ".jsx", ".css", ".html")):
                p = Path(root) / name
                try:
                    if p.stat().st_mtime > built_mtime:
                        return True
                except OSError:
                    continue
    # package.json / vite config / tsconfig changes also invalidate the build.
    for cfg in ("package.json", "vite.config.ts", "vite.config.js", "tsconfig.json"):
        p = BASE_DIR / "frontend" / cfg
        if p.is_file():
            try:
                if p.stat().st_mtime > built_mtime:
                    return True
            except OSError:
                continue
    return False


def _build_frontend_if_needed() -> None:
    """Build the React frontend on startup if dist is missing or stale.

    Silent no-op when npm is unavailable or the frontend directory is absent;
    the existing SPA-fallback warning still fires for a missing dist.
    """
    frontend_dir = BASE_DIR / "frontend"
    if not (frontend_dir / "package.json").is_file():
        return
    npm = shutil.which("npm")
    if npm is None:
        return
    if not _frontend_needs_build():
        return
    print("Building frontend (frontend/dist is missing or stale)...", file=sys.stderr)
    try:
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(frontend_dir),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"WARNING: frontend build failed to start: {exc}", file=sys.stderr)
        return
    if result.returncode != 0:
        print("WARNING: frontend build failed.", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return
    if (FRONTEND_DIST_DIR / "index.html").is_file():
        print("Frontend build complete.", file=sys.stderr)
    else:
        print("WARNING: frontend build reported success but dist/index.html is missing.", file=sys.stderr)


def _free_port_if_stale(port: int) -> None:
    """Kill any leftover process still bound to ``port`` before we bind.

    A previous `python app.py` that was killed via terminal-close (instead of
    Ctrl+C) can leave a uvicorn worker holding the port, which makes the next
    start fail with EADDRINUSE. We only kill processes whose command line
    clearly looks like our own app (contains "app.py"), so unrelated services
    on the same port are left alone and surfaced as a clear error instead.
    """
    try:
        import psutil
    except ImportError:
        return  # psutil is in requirements.txt; if missing, just attempt the bind.
    try:
        owning_pids = {
            c.pid for c in psutil.net_connections(kind="inet")
            if c.status == psutil.CONN_LISTEN and c.laddr.port == port and c.pid
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return
    for pid in owning_pids:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            continue
        try:
            cmdline = " ".join(proc.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cmdline = ""
        # Only kill processes that are clearly our own app. Anything else on
        # the port is left alone and the bind error below will tell the user.
        if "app.py" not in cmdline:
            continue
        print(
            f"Killing stale process {pid} on port {port} "
            f"({proc.name()}: {cmdline or '<cmdline unavailable>'})",
            file=sys.stderr,
        )
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        except psutil.NoSuchProcess:
            pass


def main() -> None:
    import uvicorn

    if _ffmpeg_available() is None:
        print("WARNING: ffmpeg not found on PATH. WAV conversion will fail.", file=sys.stderr)
    _build_frontend_if_needed()
    if not (FRONTEND_DIST_DIR / "index.html").is_file():
        print(
            "WARNING: frontend/dist not built. Run `npm --prefix frontend run build`.",
            file=sys.stderr,
        )

    _free_port_if_stale(8765)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
