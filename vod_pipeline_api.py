"""FastAPI integration layer for the TTVturbo VOD pipeline.

Bridges the isolated :mod:`vod_pipeline` core (no FastAPI, no React) with
the running HTTP app. It owns:

* a single :class:`VodPipelineService` instance built from a
  :class:`VodPipelineStorage` + :class:`ChannelLister`;
* the FastAPI routers for Twitch profiles, VODs and the runtime status;
* typed error -> HTTP status mapping (no text-fragment sniffing);
* the runtime diagnostic endpoint (yt-dlp + ffprobe availability).

No Twitch API credentials needed — yt-dlp handles channel listing,
metadata fetch and downloads. No client-supplied file paths ever reach
the worker. Mirrors the structure of :mod:`voice_profiles_api`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from api_utils import error_response as _error_response

from vod_pipeline import (
    TwitchClientError,
    TwitchNotFoundError,
    TwitchProfileConflictError,
    TwitchProfileNotFoundError,
    TwitchProfileStorageError,
    TwitchProfileValidationError,
    VodConflictError,
    VodNotFoundError,
    VodPipelineService,
    VodStorageError,
    VodValidationError,
)
from vod_pipeline.service import FFprobeError
from vod_pipeline.twitch_client import ChannelLister

logger = logging.getLogger("ttvturbo.vod_pipeline_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateProfileRequest(BaseModel):
    login: Optional[str] = None
    url: Optional[str] = None


class ImportVodRequest(BaseModel):
    profile_id: str
    url: str


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


# _error_response is imported from api_utils.


def _map_profile_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, TwitchProfileValidationError):
        return _error_response(400, "twitch_profile_validation", str(exc))
    if isinstance(exc, TwitchProfileNotFoundError):
        return _error_response(404, "twitch_profile_not_found", str(exc))
    if isinstance(exc, TwitchProfileConflictError):
        return _error_response(409, "twitch_profile_conflict", str(exc))
    if isinstance(exc, TwitchProfileStorageError):
        msg = str(exc)
        if msg.startswith("invalid profile id") or msg.startswith("profile id"):
            return _error_response(404, "twitch_profile_not_found", "Profile not found.")
        logger.exception("twitch-profile storage error")
        return _error_response(500, "twitch_profile_storage", "Profile storage error.")
    logger.exception("unexpected twitch-profile error")
    return _error_response(500, "twitch_profile_internal", "Internal profile error.")


def _map_vod_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, VodValidationError):
        return _error_response(400, "vod_validation", str(exc))
    if isinstance(exc, VodNotFoundError):
        return _error_response(404, "vod_not_found", str(exc))
    if isinstance(exc, VodConflictError):
        return _error_response(409, "vod_conflict", str(exc))
    if isinstance(exc, VodStorageError):
        msg = str(exc)
        if msg.startswith("invalid vod id") or msg.startswith("vod id"):
            return _error_response(404, "vod_not_found", "VOD not found.")
        logger.exception("vod storage error")
        return _error_response(500, "vod_storage", "VOD storage error.")
    logger.exception("unexpected vod error")
    return _error_response(500, "vod_internal", "Internal VOD error.")


def _map_twitch_client_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, TwitchNotFoundError):
        return _error_response(404, "twitch_not_found", str(exc))
    logger.exception("yt-dlp listing/metadata error")
    return _error_response(503, "twitch_error", str(exc))


def _map_any(exc: Exception) -> JSONResponse:
    """Map a mixed error from a service call that may raise profile/vod/twitch errors."""
    if isinstance(exc, (TwitchProfileValidationError, TwitchProfileNotFoundError,
                        TwitchProfileConflictError, TwitchProfileStorageError)):
        return _map_profile_error(exc)
    if isinstance(exc, (VodValidationError, VodNotFoundError, VodConflictError, VodStorageError)):
        return _map_vod_error(exc)
    if isinstance(exc, (TwitchNotFoundError, TwitchClientError)):
        return _map_twitch_client_error(exc)
    logger.exception("unexpected vod-pipeline error")
    return _error_response(500, "vod_pipeline_internal", "Internal VOD-pipeline error.")


# ---------------------------------------------------------------------------
# Twitch runtime diagnostic
# ---------------------------------------------------------------------------


def _yt_dlp_version() -> Optional[str]:
    try:
        import yt_dlp

        return getattr(yt_dlp.version, "__version__", None) or "unknown"
    except Exception:  # pragma: no cover - depends on environment
        return None


def _ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def _dir_writable(path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        tmp = path / ".writetest"
        with open(tmp, "wb") as fh:
            fh.write(b"x")
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.unlink()
        return True
    except OSError:
        return False


def build_twitch_status_router(service: VodPipelineService) -> APIRouter:
    """Router for GET /api/twitch/status.

    Reports yt-dlp + ffprobe availability and download-dir writability.
    No Twitch API credentials are needed — yt-dlp handles everything.
    """
    router = APIRouter(prefix="/api/twitch", tags=["twitch"])
    state = {
        "cache": None,
        "cache_ts": 0.0,
        "ttl": 10.0,
    }

    @router.get("/status")
    def twitch_status() -> JSONResponse:
        now = time.monotonic()
        cached = state["cache"]
        if cached is not None and (now - state["cache_ts"]) < state["ttl"]:
            return JSONResponse(content=cached)

        reasons: list[str] = []
        warnings: list[str] = []

        yt_dlp_version = _yt_dlp_version()
        ffprobe_ok = _ffprobe_available()
        download_dir_writable = _dir_writable(service.download_dir)

        if yt_dlp_version is None:
            reasons.append("yt-dlp is not installed or not on PATH")
        if not ffprobe_ok:
            reasons.append("ffprobe is not available on PATH")
        if not download_dir_writable:
            reasons.append("VOD download directory is not writable")

        downloader_available = bool(yt_dlp_version and ffprobe_ok and download_dir_writable)
        available = downloader_available

        payload = {
            "available": available,
            "downloader_available": downloader_available,
            "yt_dlp_version": yt_dlp_version,
            "ffprobe_available": ffprobe_ok,
            "download_dir_writable": download_dir_writable,
            "reasons": reasons,
            "warnings": warnings,
        }
        state["cache"] = payload
        state["cache_ts"] = now
        return JSONResponse(content=payload)

    return router


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_router(service: VodPipelineService) -> APIRouter:
    """Build the VOD-pipeline API router bound to a service instance."""
    state = {"service": service}
    router = APIRouter(prefix="/api", tags=["vod-pipeline"])
    router.state = state  # type: ignore[attr-defined]

    svc = service

    # ----------------------------------------------------------------- profiles
    @router.get("/twitch/profiles")
    def list_profiles() -> JSONResponse:
        try:
            profiles = svc.list_profiles()
        except Exception as exc:
            return _map_profile_error(exc)
        # Attach live VOD counts.
        out = []
        for p in profiles:
            d = dict(p)
            d["vod_count"] = svc.profile_vod_count(p["id"])
            out.append(d)
        return JSONResponse(content={"profiles": out})

    @router.post("/twitch/profiles")
    def create_profile(request: CreateProfileRequest) -> JSONResponse:
        raw = request.login or request.url
        if not raw or not str(raw).strip():
            return _error_response(
                400, "twitch_profile_validation", "login or url is required."
            )
        try:
            profile = svc.create_profile(str(raw))
        except Exception as exc:
            return _map_any(exc)
        out = dict(profile)
        out["vod_count"] = 0
        return JSONResponse(status_code=201, content=out)

    @router.get("/twitch/profiles/{profile_id}")
    def get_profile(profile_id: str) -> JSONResponse:
        try:
            profile = svc.get_profile(profile_id)
        except Exception as exc:
            return _map_profile_error(exc)
        out = dict(profile)
        out["vod_count"] = svc.profile_vod_count(profile_id)
        return JSONResponse(content=out)

    @router.post("/twitch/profiles/{profile_id}/refresh")
    def refresh_profile(profile_id: str) -> JSONResponse:
        try:
            profile = svc.refresh_profile(profile_id)
        except Exception as exc:
            return _map_any(exc)
        out = dict(profile)
        out["vod_count"] = svc.profile_vod_count(profile_id)
        return JSONResponse(content=out)

    @router.delete("/twitch/profiles/{profile_id}")
    def delete_profile(profile_id: str) -> JSONResponse:
        try:
            deleted = svc.delete_profile(profile_id)
        except TwitchProfileConflictError as exc:
            # Include the connected VOD count for a clear 409 message.
            try:
                count = svc.profile_vod_count(profile_id)
            except Exception:
                count = None
            return _error_response(
                409,
                "twitch_profile_conflict",
                str(exc),
                vod_count=count,
            )
        except Exception as exc:
            return _map_profile_error(exc)
        if not deleted:
            return _error_response(404, "twitch_profile_not_found", "Profile not found.")
        return JSONResponse(content={"id": profile_id, "deleted": True})

    @router.post("/twitch/profiles/{profile_id}/sync-vods")
    def sync_vods(profile_id: str) -> JSONResponse:
        try:
            result = svc.sync_vods(profile_id)
        except Exception as exc:
            return _map_any(exc)
        return JSONResponse(content=result)

    # ----------------------------------------------------------------- vods
    @router.get("/vods")
    def list_vods(
        profile_id: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        sort: str = Query("newest"),
    ) -> JSONResponse:
        try:
            vods = svc.list_vods(profile_id=profile_id, status=status, search=search, sort=sort)
        except Exception as exc:
            return _map_vod_error(exc)
        return JSONResponse(content={"vods": vods})

    @router.get("/vods/{vod_id}")
    def get_vod(vod_id: str) -> JSONResponse:
        try:
            vod = svc.get_vod(vod_id)
        except Exception as exc:
            return _map_vod_error(exc)
        return JSONResponse(content=vod)

    @router.post("/vods/import")
    def import_vod(request: ImportVodRequest) -> JSONResponse:
        try:
            vod = svc.import_vod(request.url, profile_id=request.profile_id)
        except Exception as exc:
            return _map_any(exc)
        return JSONResponse(status_code=201, content=vod)

    @router.post("/vods/{vod_id}/download")
    def start_download(vod_id: str) -> JSONResponse:
        try:
            vod = svc.start_download(vod_id)
        except VodConflictError as exc:
            return _error_response(409, "vod_conflict", str(exc))
        except Exception as exc:
            return _map_vod_error(exc)
        return JSONResponse(content=vod)

    @router.post("/vods/{vod_id}/cancel")
    def cancel_download(vod_id: str) -> JSONResponse:
        try:
            vod = svc.cancel_download(vod_id)
        except VodConflictError as exc:
            return _error_response(409, "vod_conflict", str(exc))
        except Exception as exc:
            return _map_vod_error(exc)
        return JSONResponse(content=vod)

    @router.post("/vods/{vod_id}/retry")
    def retry_download(vod_id: str) -> JSONResponse:
        try:
            vod = svc.retry_download(vod_id)
        except VodConflictError as exc:
            return _error_response(409, "vod_conflict", str(exc))
        except Exception as exc:
            return _map_vod_error(exc)
        return JSONResponse(content=vod)

    @router.get("/vods/{vod_id}/file")
    def get_vod_file(vod_id: str) -> FileResponse:
        try:
            path = svc.ready_file_path(vod_id)
        except VodNotFoundError:
            return _error_response(404, "vod_not_found", "VOD not found.")
        except Exception as exc:
            return _map_vod_error(exc)
        if path is None:
            return _error_response(
                409, "vod_not_ready", "File is only available for READY VODs."
            )
        return FileResponse(path, filename=path.name)

    @router.get("/vods/{vod_id}/stream-download")
    def stream_download(vod_id: str):
        """On-demand browser download: stream the VOD via yt-dlp directly
        to the browser without persisting it on the server.

        Unlike ``POST /vods/{id}/download`` (which is used by the VOD
        Pipeline and stores the file on disk for downstream processing),
        this endpoint pipes yt-dlp's stdout directly to the HTTP response
        so bytes flow to the browser as soon as the download starts — the
        browser's download manager shows real-time progress immediately.
        The VOD's persisted status is not modified.
        """
        try:
            vod = svc.get_vod(vod_id)
        except VodNotFoundError:
            return _error_response(404, "vod_not_found", "VOD not found.")
        except Exception as exc:
            return _map_vod_error(exc)
        source_url = vod.get("source_url")
        if not source_url:
            return _error_response(409, "vod_no_source", "VOD has no source URL.")
        title = vod.get("title") or vod.get("twitch_video_id") or vod_id
        # Sanitize the title into a safe filename stem.
        safe_stem = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in title).strip() or vod_id
        filename = f"{safe_stem}.mp4"

        try:
            proc = subprocess.Popen(
                [
                    "python", "-m", "yt_dlp",
                    "-o", "-",
                    "--no-playlist",
                    "--quiet",
                    "--no-warnings",
                    "--format", "best",
                    source_url,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            return _error_response(503, "yt_dlp_missing", "yt-dlp is not installed.")
        except Exception as exc:
            return _error_response(500, "stream_download_failed", str(exc))

        # Read the first chunk synchronously so we can detect immediate
        # errors (e.g. video doesn't exist) and return a proper error
        # response instead of an empty/invalid download. Once we yield the
        # first chunk the response headers are committed.
        try:
            first_chunk = proc.stdout.read(65536)
        except Exception as exc:
            proc.kill()
            proc.stdout.close()
            proc.stderr.close()
            return _error_response(500, "stream_download_failed", str(exc))

        if not first_chunk:
            proc.wait()
            stderr = proc.stderr.read().decode("utf-8", errors="replace").strip()
            proc.stdout.close()
            proc.stderr.close()
            return _error_response(
                502, "yt_dlp_failed",
                f"yt-dlp download failed: {stderr or 'no output produced'}",
            )

        def _stream():
            try:
                yield first_chunk
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.wait()
                if proc.returncode != 0:
                    stderr = proc.stderr.read().decode("utf-8", errors="replace")
                    logger.error(
                        "yt-dlp stream for %s ended with code %d: %s",
                        vod_id, proc.returncode, stderr,
                    )
                proc.stdout.close()
                proc.stderr.close()

        return StreamingResponse(
            _stream(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @router.delete("/vods/{vod_id}")
    def delete_vod(vod_id: str) -> JSONResponse:
        try:
            deleted = svc.delete_vod(vod_id)
        except Exception as exc:
            return _map_vod_error(exc)
        if not deleted:
            return _error_response(404, "vod_not_found", "VOD not found.")
        return JSONResponse(content={"id": vod_id, "deleted": True})

    @router.get("/vods/{vod_id}/log")
    def get_vod_log(vod_id: str) -> JSONResponse:
        try:
            excerpt = svc.worker_log_excerpt(vod_id)
        except VodNotFoundError:
            return _error_response(404, "vod_not_found", "VOD not found.")
        except Exception as exc:
            return _map_vod_error(exc)
        if excerpt is None:
            return _error_response(404, "vod_log_not_found", "No worker log for this VOD.")
        return JSONResponse(content={"id": vod_id, "log": excerpt})

    return router


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------


def build_service(
    data_dir,
    download_dir,
    max_concurrent: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
    sync_limit: Optional[int] = None,
    library_service=None,
) -> VodPipelineService:
    """Build a :class:`VodPipelineService` instance.

    No Twitch API credentials needed — yt-dlp handles channel listing,
    metadata fetch and downloads. Reads concurrency / timeout / sync-limit
    from environment variables with sensible defaults.
    """
    from pathlib import Path

    from vod_pipeline import VodPipelineStorage
    from vod_pipeline.schemas import DEFAULT_SYNC_LIMIT
    from vod_pipeline.service import (
        DEFAULT_MAX_CONCURRENT,
        DEFAULT_TIMEOUT_SECONDS,
    )
    from vod_pipeline.twitch_client import DEFAULT_TIMEOUT_SECONDS as LISTER_TIMEOUT

    mc = max_concurrent
    if mc is None:
        env_mc = os.environ.get("TTVTURBO_MAX_CONCURRENT_VOD_DOWNLOADS")
        mc = int(env_mc) if env_mc and env_mc.isdigit() else DEFAULT_MAX_CONCURRENT
    to = timeout_seconds
    if to is None:
        env_to = os.environ.get("TTVTURBO_VOD_DOWNLOAD_TIMEOUT_SECONDS")
        if env_to:
            try:
                to = float(env_to)
            except ValueError:
                to = DEFAULT_TIMEOUT_SECONDS
        else:
            to = DEFAULT_TIMEOUT_SECONDS
    sl = sync_limit
    if sl is None:
        env_sl = os.environ.get("TTVTURBO_VOD_SYNC_LIMIT")
        sl = int(env_sl) if env_sl and env_sl.isdigit() else DEFAULT_SYNC_LIMIT

    storage = VodPipelineStorage(Path(data_dir))
    lister = ChannelLister(timeout_seconds=LISTER_TIMEOUT)
    service = VodPipelineService(
        storage=storage,
        channel_lister=lister,
        download_dir=Path(download_dir),
        max_concurrent=int(mc),
        timeout_seconds=float(to),
        sync_limit=int(sl),
        library_service=library_service,
    )
    return service
