"""Voice Clone API router — ``/api/voice-clone/*``.

Extracted from ``app_factory.py`` so the factory stays a thin wiring
layer.  The handlers read from a :class:`ServiceContainer`-like object
at request time.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ttvturbo.voice_clone.schemas import CreateGenerationRequest
from ttvturbo.voice_clone.service import ValidationError as VoiceCloneValidationError


def build_voice_clone_router(container: Any) -> APIRouter:
    """Build the voice-clone router.

    *container* must expose ``voice_clone_service``.
    """
    router = APIRouter(tags=["voice-clone"])

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

    return router
