"""FastAPI integration layer for the shared media-processing core.

Bridges :mod:`media_processing` (no FastAPI) with the running HTTP app.

Endpoints:

  GET    /api/transcription/status
  POST   /api/transcription/preload-model
  POST   /api/transcriptions
  GET    /api/transcriptions
  GET    /api/transcriptions/{id}
  POST   /api/transcriptions/{id}/cancel
  POST   /api/transcriptions/{id}/retry
  DELETE /api/transcriptions/{id}
  GET    /api/transcriptions/{id}/json
  GET    /api/transcriptions/{id}/txt
  GET    /api/transcriptions/{id}/srt
  GET    /api/transcriptions/{id}/vtt
  GET    /api/vods/{vod_id}/transcriptions

  POST   /api/pipeline-runs
  GET    /api/pipeline-runs
  GET    /api/pipeline-runs/{id}
  POST   /api/pipeline-runs/{id}/cancel
  POST   /api/pipeline-runs/{id}/retry
  DELETE /api/pipeline-runs/{id}
  GET    /api/vods/{vod_id}/pipeline-runs

  GET    /api/vods/{vod_id}/artifacts/audio   (audio artifact metadata)
  POST   /api/vods/{vod_id}/artifacts/audio   (start extraction)
  GET    /api/vods/{vod_id}/artifacts/audio/file  (stream FLAC)

Mirrors the structure of :mod:`vod_pipeline_api`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ttvturbo.api_utils import error_response as _error_response

from ttvturbo.media_processing import (
    AudioExtractionError,
    AudioExtractionService,
    GpuLockBusyError,
    GpuLockError,
    MediaJobConflictError,
    MediaJobNotFoundError,
    MediaJobValidationError,
    MediaSourceError,
    MediaSourceNotFoundError,
    MediaSourceNotReadyError,
    PipelineRunConflictError,
    PipelineRunNotFoundError,
    PipelineRunValidationError,
    PipelineService,
    TranscriptionError,
    TranscriptionService,
    UploadStorage,
)
from ttvturbo.media_processing.schemas import (
    MediaJobStatus,
    TranscriptionStatus,
)

logger = logging.getLogger("ttvturbo.media_processing_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StartTranscriptionRequest(BaseModel):
    source_type: str = "twitch_vod"
    source_id: str
    language: Optional[str] = None
    model: Optional[str] = None
    model_family: Optional[str] = None  # "whisper", "parakeet", "canary"
    hotwords: Optional[str] = None
    force_audio_extraction: bool = False


class StartPipelineRunRequest(BaseModel):
    source_type: str = "twitch_vod"
    source_id: str


class StartAudioExtractionRequest(BaseModel):
    force: bool = False


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


# _error_response is imported from ttvturbo.api_utils.


def _map_media_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, MediaSourceNotFoundError):
        return _error_response(404, "media_source_not_found", str(exc))
    if isinstance(exc, MediaSourceNotReadyError):
        return _error_response(409, "media_source_not_ready", str(exc))
    if isinstance(exc, MediaSourceError):
        return _error_response(400, "media_source_invalid", str(exc))
    if isinstance(exc, MediaJobNotFoundError):
        return _error_response(404, "media_job_not_found", str(exc))
    if isinstance(exc, MediaJobValidationError):
        return _error_response(400, "media_job_validation", str(exc))
    if isinstance(exc, MediaJobConflictError):
        return _error_response(409, "media_job_conflict", str(exc))
    if isinstance(exc, TranscriptionError):
        return _error_response(409, "transcription_unavailable", str(exc))
    if isinstance(exc, AudioExtractionError):
        return _error_response(500, "audio_extraction_error", str(exc))
    if isinstance(exc, GpuLockBusyError):
        owner = exc.owner or {}
        return _error_response(
            409, "gpu_busy", "GPU is busy.",
            owner_type=owner.get("owner_type"),
        )
    if isinstance(exc, GpuLockError):
        return _error_response(500, "gpu_lock_error", str(exc))
    logger.exception("unexpected media-processing error")
    return _error_response(500, "media_internal", "Internal media-processing error.")


def _map_pipeline_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, PipelineRunNotFoundError):
        return _error_response(404, "pipeline_run_not_found", str(exc))
    if isinstance(exc, PipelineRunValidationError):
        return _error_response(400, "pipeline_run_validation", str(exc))
    if isinstance(exc, PipelineRunConflictError):
        return _error_response(409, "pipeline_run_conflict", str(exc))
    logger.exception("unexpected pipeline error")
    return _error_response(500, "pipeline_internal", "Internal pipeline error.")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_media_processing_router(
    audio_service: AudioExtractionService,
    transcription_service: TranscriptionService,
    pipeline_service: PipelineService,
    upload_storage: Optional[UploadStorage] = None,
    library_service=None,
) -> APIRouter:
    """Build the media-processing API router bound to service instances.

    ``library_service`` is the new persistent video store. When provided,
    upload endpoints create library items instead of using the legacy
    ``UploadStorage``. The ``upload_storage`` parameter is kept for
    backward compatibility (transcription of legacy uploads).
    """
    router = APIRouter(prefix="/api", tags=["media-processing"])

    # ----------------------------------------------------------------- transcription status
    @router.get("/transcription/status")
    def transcription_status() -> JSONResponse:
        status = transcription_service.runtime_status()
        return JSONResponse(content=status)

    # ----------------------------------------------------------------- transcription model preload
    @router.post("/transcription/preload-model")
    async def transcription_preload_model(request: Request) -> JSONResponse:
        """Pre-download the configured faster-whisper model into the HF cache.

        Runs the (potentially long) download in a worker thread so the
        event loop stays responsive. Returns once the download finishes.
        """
        import asyncio
        from functools import partial

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, partial(transcription_service.preload_model))
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error", "model preload failed"))
        return JSONResponse(content=result)

    # ----------------------------------------------------------------- transcriptions
    @router.post("/transcriptions")
    def start_transcription(request: StartTranscriptionRequest) -> JSONResponse:
        try:
            job = transcription_service.start_transcription(
                source_type=request.source_type,
                source_id=request.source_id,
                language=request.language,
                model=request.model,
                model_family=request.model_family,
                hotwords=request.hotwords,
                force_audio_extraction=request.force_audio_extraction,
            )
        except Exception as exc:
            return _map_media_error(exc)
        return JSONResponse(status_code=201, content=job)

    @router.post("/transcriptions/upload")
    async def upload_and_transcribe(
        file: UploadFile = File(...),
        language: Optional[str] = None,
        model: Optional[str] = None,
        model_family: Optional[str] = None,
        hotwords: Optional[str] = None,
    ) -> JSONResponse:
        """Upload a media file and start a transcription for it.

        The file is stored as a library item and transcribed directly.
        """
        if library_service is None and upload_storage is None:
            return _error_response(503, "uploads_disabled", "File uploads are not configured.")
        if not file.filename:
            return _error_response(400, "upload_validation", "File name is required.")
        # Save the uploaded file via the library (preferred) or legacy upload storage.
        if library_service is not None:
            try:
                meta = library_service.create_upload_item(file_name=file.filename, title=file.filename)
                upload_id = meta["id"]
                content = await file.read()
                dest = library_service.storage.write_item_file(upload_id, file.filename, content)
                meta["file_size_bytes"] = dest.stat().st_size
                library_service.storage.save_item(meta)
            except Exception as exc:
                try:
                    library_service.delete_item(upload_id)
                except Exception:
                    pass
                return _error_response(500, "upload_save_failed", str(exc))
            finally:
                await file.close()
        else:
            meta = upload_storage.create_upload(file_name=file.filename, title=file.filename)
            upload_id = meta["id"]
            upload_dir = upload_storage.upload_dir(upload_id)
            dest = upload_dir / file.filename
            try:
                content = await file.read()
                with open(dest, "wb") as fh:
                    fh.write(content)
            except Exception as exc:
                upload_storage.delete_upload(upload_id)
                return _error_response(500, "upload_save_failed", str(exc))
            finally:
                await file.close()
        # Start transcription for the uploaded file.
        try:
            job = transcription_service.start_transcription(
                source_type="file_upload",
                source_id=upload_id,
                language=language,
                model=model,
                model_family=model_family,
                hotwords=hotwords,
            )
        except Exception as exc:
            if library_service is not None:
                library_service.delete_item(upload_id)
            else:
                upload_storage.delete_upload(upload_id)
            return _map_media_error(exc)
        return JSONResponse(status_code=201, content=job)

    # ------------------------------------------------------------------
    # Library upload endpoints are in library_api.py.  The legacy
    # /library/uploads/* endpoints (GET list, GET file, DELETE) were
    # removed after verifying the frontend only uses /api/library/items
    # and POST /api/library/uploads.
    # ------------------------------------------------------------------

    @router.get("/transcriptions")
    def list_transcriptions(
        source_id: Optional[str] = Query(default=None),
    ) -> JSONResponse:
        # Recover any WAITING_FOR_DEPENDENCY jobs whose audio became READY.
        transcription_service.poll_dependencies()
        # Merge job records with transcript metadata for a unified view.
        jobs = transcription_service.list_jobs(source_id=source_id)
        # Index transcripts by id.
        transcripts = transcription_service.list_transcriptions(vod_id=source_id)
        t_by_id = {t.get("id"): t for t in transcripts}
        out = []
        for job in jobs:
            rec = dict(job)
            tid = job.get("transcription_id")
            if tid and tid in t_by_id:
                tmeta = t_by_id[tid]
                rec["transcript"] = tmeta
                # If the job is READY but the transcript status is not set,
                # surface the transcript status.
                tstatus = tmeta.get("status")
                if tstatus:
                    rec["transcript_status"] = tstatus
            out.append(rec)
        # Also include transcripts that have no job (e.g. job deleted but
        # transcript kept) — but in this phase every transcript has a job.
        return JSONResponse(content={"transcriptions": out})

    @router.get("/transcriptions/{transcription_id}")
    def get_transcription(transcription_id: str) -> JSONResponse:
        try:
            # Recover any WAITING_FOR_DEPENDENCY jobs whose audio became READY.
            transcription_service.poll_dependencies()
            # Find the job by transcription_id.
            job = None
            for j in transcription_service.list_jobs():
                if j.get("transcription_id") == transcription_id:
                    job = j
                    break
            if job is None:
                raise MediaJobNotFoundError(f"transcription not found: {transcription_id}")
            tmeta = None
            try:
                tmeta = transcription_service.get_transcription(transcription_id)
            except MediaJobNotFoundError:
                pass
            rec = dict(job)
            if tmeta is not None:
                rec["transcript"] = tmeta
                rec["transcript_status"] = tmeta.get("status")
            return JSONResponse(content=rec)
        except Exception as exc:
            return _map_media_error(exc)

    @router.post("/transcriptions/{transcription_id}/cancel")
    def cancel_transcription(transcription_id: str) -> JSONResponse:
        try:
            job = None
            for j in transcription_service.list_jobs():
                if j.get("transcription_id") == transcription_id:
                    job = j
                    break
            if job is None:
                raise MediaJobNotFoundError(f"transcription not found: {transcription_id}")
            result = transcription_service.cancel_job(job["id"])
            return JSONResponse(content=result)
        except Exception as exc:
            return _map_media_error(exc)

    @router.post("/transcriptions/{transcription_id}/retry")
    def retry_transcription(transcription_id: str) -> JSONResponse:
        try:
            job = None
            for j in transcription_service.list_jobs():
                if j.get("transcription_id") == transcription_id:
                    job = j
                    break
            if job is None:
                raise MediaJobNotFoundError(f"transcription not found: {transcription_id}")
            result = transcription_service.retry_job(job["id"])
            return JSONResponse(content=result)
        except Exception as exc:
            return _map_media_error(exc)

    @router.delete("/transcriptions/{transcription_id}")
    def delete_transcription(transcription_id: str) -> JSONResponse:
        try:
            transcription_service.delete_transcription(transcription_id)
            return JSONResponse(content={"id": transcription_id, "deleted": True})
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/transcriptions/{transcription_id}/json")
    def get_transcript_json(transcription_id: str) -> Any:
        try:
            path = transcription_service.transcript_file_path(transcription_id, "json")
            return FileResponse(path, media_type="application/json", filename="transcript.json")
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/transcriptions/{transcription_id}/txt")
    def get_transcript_txt(transcription_id: str) -> Any:
        try:
            path = transcription_service.transcript_file_path(transcription_id, "txt")
            return FileResponse(path, media_type="text/plain", filename="transcript.txt")
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/transcriptions/{transcription_id}/srt")
    def get_transcript_srt(transcription_id: str) -> Any:
        try:
            path = transcription_service.transcript_file_path(transcription_id, "srt")
            return FileResponse(path, media_type="text/plain", filename="transcript.srt")
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/transcriptions/{transcription_id}/vtt")
    def get_transcript_vtt(transcription_id: str) -> Any:
        try:
            path = transcription_service.transcript_file_path(transcription_id, "vtt")
            return FileResponse(path, media_type="text/vtt", filename="transcript.vtt")
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/vods/{vod_id}/transcriptions")
    def list_vod_transcriptions(vod_id: str) -> JSONResponse:
        try:
            # Recover any WAITING_FOR_DEPENDENCY jobs whose audio became READY.
            transcription_service.poll_dependencies()
            transcripts = transcription_service.list_transcriptions(vod_id=vod_id)
            return JSONResponse(content={"transcriptions": transcripts})
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/sources/{source_type}/{source_id}/transcriptions")
    def list_source_transcriptions(source_type: str, source_id: str) -> JSONResponse:
        """List transcriptions for any supported source (twitch_vod, file_upload)."""
        try:
            transcription_service.poll_dependencies()
            transcripts = transcription_service.list_transcriptions(vod_id=source_id)
            return JSONResponse(content={"transcriptions": transcripts})
        except Exception as exc:
            return _map_media_error(exc)

    # ----------------------------------------------------------------- audio artifacts
    @router.get("/vods/{vod_id}/artifacts/audio")
    def get_audio_artifact(vod_id: str) -> JSONResponse:
        try:
            meta = audio_service.get_audio_artifact(vod_id)
            if meta is None:
                return _error_response(404, "audio_artifact_not_found", "No audio artifact for this VOD.")
            return JSONResponse(content=meta)
        except Exception as exc:
            return _map_media_error(exc)

    @router.post("/vods/{vod_id}/artifacts/audio")
    def start_audio_extraction(vod_id: str, request: StartAudioExtractionRequest) -> JSONResponse:
        try:
            job = audio_service.start_extraction("twitch_vod", vod_id, force=request.force)
            return JSONResponse(status_code=201, content=job)
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/vods/{vod_id}/artifacts/audio/file")
    def get_audio_file(vod_id: str) -> Any:
        try:
            meta = audio_service.get_audio_artifact(vod_id)
            if meta is None:
                return _error_response(404, "audio_artifact_not_found", "No audio artifact for this VOD.")
            path = audio_service.artifact_path(vod_id)
            if not path.is_file():
                return _error_response(404, "audio_artifact_not_found", "Audio file is missing on disk.")
            return FileResponse(path, media_type="audio/flac", filename="source_audio.flac")
        except Exception as exc:
            return _map_media_error(exc)

    # ----------------------------------------------------------------- audio artifacts (generic source)
    @router.get("/sources/{source_type}/{source_id}/artifacts/audio")
    def get_source_audio_artifact(source_type: str, source_id: str) -> JSONResponse:
        try:
            meta = audio_service.get_audio_artifact(source_id, source_type)
            if meta is None:
                return _error_response(404, "audio_artifact_not_found", "No audio artifact for this source.")
            return JSONResponse(content=meta)
        except Exception as exc:
            return _map_media_error(exc)

    @router.post("/sources/{source_type}/{source_id}/artifacts/audio")
    def start_source_audio_extraction(source_type: str, source_id: str, request: StartAudioExtractionRequest) -> JSONResponse:
        try:
            job = audio_service.start_extraction(source_type, source_id, force=request.force)
            return JSONResponse(status_code=201, content=job)
        except Exception as exc:
            return _map_media_error(exc)

    @router.get("/sources/{source_type}/{source_id}/artifacts/audio/file")
    def get_source_audio_file(source_type: str, source_id: str) -> Any:
        try:
            meta = audio_service.get_audio_artifact(source_id, source_type)
            if meta is None:
                return _error_response(404, "audio_artifact_not_found", "No audio artifact for this source.")
            path = audio_service.artifact_path(source_id, source_type)
            if not path.is_file():
                return _error_response(404, "audio_artifact_not_found", "Audio file is missing on disk.")
            return FileResponse(path, media_type="audio/flac", filename="source_audio.flac")
        except Exception as exc:
            return _map_media_error(exc)

    # ----------------------------------------------------------------- pipeline runs
    @router.post("/pipeline-runs")
    def start_pipeline_run(request: StartPipelineRunRequest) -> JSONResponse:
        try:
            run = pipeline_service.start_run(request.source_type, request.source_id)
            return JSONResponse(status_code=201, content=run)
        except Exception as exc:
            return _map_pipeline_error(exc)

    @router.get("/pipeline-runs")
    def list_pipeline_runs(source_id: Optional[str] = Query(default=None)) -> JSONResponse:
        runs = pipeline_service.list_runs(source_id=source_id)
        return JSONResponse(content={"pipeline_runs": runs})

    @router.get("/pipeline-runs/{run_id}")
    def get_pipeline_run(run_id: str) -> JSONResponse:
        try:
            run = pipeline_service.get_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_pipeline_error(exc)

    @router.post("/pipeline-runs/{run_id}/cancel")
    def cancel_pipeline_run(run_id: str) -> JSONResponse:
        try:
            run = pipeline_service.cancel_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_pipeline_error(exc)

    @router.post("/pipeline-runs/{run_id}/retry")
    def retry_pipeline_run(run_id: str) -> JSONResponse:
        try:
            run = pipeline_service.retry_run(run_id)
            return JSONResponse(content=run)
        except Exception as exc:
            return _map_pipeline_error(exc)

    @router.delete("/pipeline-runs/{run_id}")
    def delete_pipeline_run(run_id: str) -> JSONResponse:
        try:
            pipeline_service.delete_run(run_id)
            return JSONResponse(content={"id": run_id, "deleted": True})
        except Exception as exc:
            return _map_pipeline_error(exc)

    @router.get("/vods/{vod_id}/pipeline-runs")
    def list_vod_pipeline_runs(vod_id: str) -> JSONResponse:
        runs = pipeline_service.list_runs(source_id=vod_id)
        return JSONResponse(content={"pipeline_runs": runs})

    return router
