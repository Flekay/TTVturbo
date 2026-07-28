"""FastAPI integration layer for the TTVturbo voice-profile feature.

Bridges the isolated :mod:`voice_profiles` core (no FastAPI, no React, no
Qwen3-TTS imports) with the running HTTP app. It owns:

* a single :class:`VoiceProfileService` instance built from a
  :class:`ScriptLibrary` + :class:`VoiceProfileStorage`;
* the FastAPI router with the voice-profile endpoints;
* typed error -> HTTP status mapping (no text-fragment sniffing);
* server-side technical quality analysis by delegating to the existing
  :mod:`voice_clone.quality` analyzer via :class:`VoiceCloneService`.

No second quality analyzer, no fake scores, no client-supplied script text
or quality values ever reach the persisted profile.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api_utils import error_response as _error_response

from voice_clone.service import VoiceCloneService
from voice_profiles import (
    VoiceProfileConflictError,
    VoiceProfileNotFoundError,
    VoiceProfileStorageError,
    VoiceProfileValidationError,
    VoiceScriptNotFoundError,
)
from voice_profiles.library import ScriptLibrary
from voice_profiles.schemas import EXPECTED_PACK_PROMPT_COUNT, ReferenceStatus
from voice_profiles.service import VoiceProfileService
from voice_profiles.storage import VoiceProfileStorage

logger = logging.getLogger("ttvturbo.voice_profiles_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateProfileRequest(BaseModel):
    name: str
    locale: str = "de-DE"


class PatchProfileRequest(BaseModel):
    name: Optional[str] = None


class AttachReferenceRequest(BaseModel):
    recording_filename: str


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


# _error_response is imported from api_utils.


def _map_voice_profile_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, VoiceProfileValidationError):
        return _error_response(400, "voice_profile_validation", str(exc))
    if isinstance(exc, VoiceProfileNotFoundError):
        return _error_response(404, "voice_profile_not_found", str(exc))
    if isinstance(exc, VoiceScriptNotFoundError):
        return _error_response(404, "voice_script_not_found", str(exc))
    if isinstance(exc, VoiceProfileConflictError):
        return _error_response(409, "voice_profile_conflict", str(exc))
    if isinstance(exc, VoiceProfileStorageError):
        # An invalid profile id (not a canonical UUID) is a client error:
        # no profile can exist with it. Treat as 404, not 500.
        msg = str(exc)
        if msg.startswith("invalid profile id") or msg.startswith("profile id must") or msg.startswith("profile id escapes"):
            return _error_response(404, "voice_profile_not_found", "Profile not found.")
        logger.exception("voice-profile storage error")
        return _error_response(500, "voice_profile_storage", "Profile storage error.")
    # Should not happen: every voice-profile error is a VoiceProfileError subclass.
    logger.exception("unexpected voice-profile error")
    return _error_response(500, "voice_profile_internal", "Internal voice-profile error.")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _prompt_to_dict(prompt: Any) -> dict:
    """Serialize a ScriptPrompt to the public API shape."""
    dur = prompt.recommended_duration_seconds
    return {
        "id": prompt.id,
        "order": prompt.order,
        "category": prompt.category,
        "style": prompt.style,
        "text": prompt.text,
        "recommended_duration_seconds": {
            "min": dur.min,
            "max": dur.max,
        },
        "tags": list(prompt.tags),
        "recording_notes": prompt.recording_notes,
    }


def _pack_meta(meta: dict) -> dict:
    """Reduce the full pack metadata to the public subset."""
    return {
        "pack_id": meta.get("pack_id"),
        "locale": meta.get("locale"),
        "title": meta.get("title"),
        "prompt_count": meta.get("prompt_count"),
    }


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_router(service: VoiceProfileService, quality_analyzer=None) -> APIRouter:
    """Build the voice-profile API router bound to a service instance.

    ``quality_analyzer`` is an optional callable ``(filename) -> dict`` that
    runs the real server-side audio quality analysis. When omitted, the
    attach-reference endpoint returns a clean 500 instead of fabricating
    scores.

    The endpoints reference ``service`` and ``quality_analyzer`` through a
    mutable container so tests can swap them without rebuilding the router.
    """
    # Mutable container so tests can swap the service/analyzer without
    # rebuilding the router or duplicating routes.
    state = {"service": service, "quality_analyzer": quality_analyzer}
    router = APIRouter(prefix="/api/voice-profiles", tags=["voice-profiles"])
    router.state = state  # type: ignore[attr-defined]

    # ----------------------------------------------------------------- scripts
    @router.get("/scripts")
    def list_scripts() -> JSONResponse:
        svc = state["service"]
        try:
            prompts = svc.library.get_recording_prompts()
            meta = svc.library.get_pack_metadata()
        except Exception as exc:  # noqa: BLE001 - surface as 500 with clean message
            logger.exception("script library unavailable")
            return _error_response(
                500, "script_library_unavailable", "Script library could not be loaded."
            )
        return JSONResponse(
            content={
                "pack": _pack_meta(meta),
                "prompts": [_prompt_to_dict(p) for p in prompts],
            }
        )

    # ----------------------------------------------------------------- profiles
    @router.get("")
    def list_profiles() -> JSONResponse:
        svc = state["service"]
        try:
            profiles = svc.list_profiles()
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(content={"profiles": profiles})

    @router.post("")
    def create_profile(request: CreateProfileRequest) -> JSONResponse:
        svc = state["service"]
        try:
            profile = svc.create_profile(name=request.name, locale=request.locale)
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(status_code=201, content=profile)

    @router.get("/{profile_id}")
    def get_profile(profile_id: str) -> JSONResponse:
        svc = state["service"]
        try:
            profile = svc.get_profile(profile_id)
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(content=profile)

    @router.patch("/{profile_id}")
    def patch_profile(profile_id: str, request: PatchProfileRequest) -> JSONResponse:
        svc = state["service"]
        if request.name is None:
            return _error_response(
                400,
                "voice_profile_validation",
                "'name' must be provided.",
            )
        try:
            profile = svc.rename_profile(profile_id, request.name)
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(content=profile)

    @router.delete("/{profile_id}")
    def delete_profile(profile_id: str) -> JSONResponse:
        svc = state["service"]
        try:
            deleted = svc.delete_profile(profile_id)
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(content={"id": profile_id, "deleted": deleted})

    # ----------------------------------------------------------------- references
    @router.put("/{profile_id}/references/{script_id}")
    def attach_reference(
        profile_id: str, script_id: str, request: AttachReferenceRequest
    ) -> JSONResponse:
        svc = state["service"]
        qa = state["quality_analyzer"]
        # 1. Run the real server-side quality analysis first. This raises a
        #    VoiceCloneValidationError (mapped to 400) on bad filenames or
        #    unreadable audio. We do NOT trust any client-supplied quality.
        if qa is None:
            return _error_response(
                500,
                "reference_analysis_unavailable",
                "Reference quality analysis is not available on this server.",
            )
        try:
            quality = qa(request.recording_filename)
        except Exception as exc:
            logger.exception("reference quality analysis failed")
            return _error_response(
                400, "reference_analysis_failed", str(exc)
            )
        try:
            profile = svc.attach_reference(
                profile_id=profile_id,
                script_id=script_id,
                recording_filename=request.recording_filename,
                quality=quality,
            )
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(content=profile)

    @router.delete("/{profile_id}/references/{script_id}")
    def detach_reference(profile_id: str, script_id: str) -> JSONResponse:
        svc = state["service"]
        try:
            profile = svc.detach_reference(profile_id, script_id)
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(content=profile)

    @router.post("/{profile_id}/references/{script_id}/accept-review")
    def accept_review(profile_id: str, script_id: str) -> JSONResponse:
        svc = state["service"]
        try:
            profile = svc.accept_review_reference(profile_id, script_id)
        except Exception as exc:
            return _map_voice_profile_error(exc)
        return JSONResponse(content=profile)

    return router


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------


def build_service(
    recordings_dir,
    voice_profiles_dir,
    pack_path=None,
) -> VoiceProfileService:
    """Build the single VoiceProfileService instance used by the app.

    The script library is validated eagerly so a missing/invalid pack is
    reported at startup. A failure here does NOT crash the app: the caller
    decides how to surface it (the endpoints return a clean 500).
    """
    library = ScriptLibrary(
        pack_path=pack_path or ScriptLibrary().pack_path,
    )
    storage = VoiceProfileStorage(voice_profiles_dir)
    service = VoiceProfileService(
        library=library,
        storage=storage,
        recordings_dir=recordings_dir,
    )
    # Eagerly validate the script pack so a bad config is logged at startup.
    try:
        prompts = library.get_recording_prompts()
        logger.info(
            "voice-profile script pack loaded: %d prompts (expected %d)",
            len(prompts),
            EXPECTED_PACK_PROMPT_COUNT,
        )
    except Exception as exc:
        logger.warning("voice-profile script pack could not be loaded: %s", exc)
    return service


def make_quality_analyzer(voice_clone_service: Optional[VoiceCloneService]):
    """Return a callable(filename) -> quality_dict using the real analyzer."""
    if voice_clone_service is None:
        return None
    analyzer = voice_clone_service

    def _analyze(filename: str) -> dict:
        # VoiceCloneService.analyze_reference already validates the path
        # safely and runs the real analyzer from voice_clone.quality.
        return analyzer.analyze_reference(filename)

    return _analyze
