"""Status API router — ``GET /api/status``.

Extracted from ``app_factory.py`` so the factory stays a thin wiring
layer.  The handler reads from a :class:`ServiceContainer`-like object
at request time, so the router can be registered before the lifespan
populates the services.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ttvturbo.settings import APP_NAME, APP_VERSION
from ttvturbo.system.executables import find_executable

logger = logging.getLogger("ttvturbo")


def _read_wav_duration(path: Path) -> float | None:
    import wave

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


def build_status_router(container: Any) -> APIRouter:
    """Build the status router.

    *container* must expose ``paths``, ``settings``, ``start_time_monotonic``,
    ``voice_clone_service``, ``voice_profile_service``, ``vod_pipeline_service``,
    ``audio_extraction_service``, ``transcription_service``, and
    ``pipeline_service`` attributes (populated by the lifespan).
    """
    import shutil

    router = APIRouter(tags=["status"])

    def _recordings_dir() -> Path:
        assert container.paths is not None
        return container.paths.recordings

    def _list_recordings() -> list[dict]:
        import datetime as _dt

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

    return router
