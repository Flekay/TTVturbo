"""FastAPI integration for the ASR preset + benchmark system.

Endpoints:

  GET    /api/asr/presets
  GET    /api/asr/status
  GET    /api/asr/default
  POST   /api/asr/default
  GET    /api/asr/models
  GET    /api/asr/audio-diagnostics/{source_type}/{source_id}
  POST   /api/asr/audio-diagnostics
  POST   /api/asr/benchmarks
  GET    /api/asr/benchmarks
  GET    /api/asr/benchmarks/{id}
  POST   /api/asr/benchmarks/{id}/start
  POST   /api/asr/benchmarks/{id}/cancel
  DELETE /api/asr/benchmarks/{id}
  POST   /api/asr/benchmarks/{id}/select-default
  GET    /api/asr/benchmarks/{id}/runs/{preset_id}

The router is bound to service instances built in ``app.py``. No free
model ids or shell arguments are accepted from the client — only known
preset ids.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from api_utils import error_response as _err

from media_processing import (
    AUDIO_VARIANTS,
    AsrBenchmarkError,
    AsrBenchmarkNotFoundError,
    AsrBenchmarkService,
    AsrDefaultPresetStore,
    AsrPresetError,
    AsrPresetNotFoundError,
    AudioForensicsService,
    is_production_eligible,
    list_presets,
)

logger = logging.getLogger("ttvturbo.asr_api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateBenchmarkRequest(BaseModel):
    source_type: str = "twitch_clip"
    source_id: str
    preset_ids: list[str] = Field(default_factory=list)
    candidate_ids: Optional[list[str]] = None
    audio_variant: Optional[str] = None
    reference_text: Optional[str] = None
    hotwords: Optional[str] = None


class CreateAudioDiagnosticRequest(BaseModel):
    source_type: str = "twitch_clip"
    source_id: str
    audio_stream_id: Optional[int] = None


class SelectDefaultRequest(BaseModel):
    preset_id: str


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


# _err is imported from api_utils (as error_response).


def _map(exc: Exception) -> JSONResponse:
    if isinstance(exc, AsrPresetNotFoundError):
        return _err(404, "preset_not_found", str(exc))
    if isinstance(exc, AsrBenchmarkNotFoundError):
        return _err(404, "benchmark_not_found", str(exc))
    if isinstance(exc, AsrPresetError):
        return _err(400, "preset_invalid", str(exc))
    if isinstance(exc, AsrBenchmarkError):
        return _err(409, "benchmark_conflict", str(exc))
    logger.exception("unexpected asr error")
    return _err(500, "asr_internal", "Internal ASR error.")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_asr_router(
    benchmark_service: AsrBenchmarkService,
    default_store: AsrDefaultPresetStore,
    forensics_service: Optional[AudioForensicsService] = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/asr", tags=["asr"])

    # ----------------------------------------------------------------- presets
    @router.get("/presets")
    def get_presets() -> JSONResponse:
        return JSONResponse(content={"presets": list_presets()})

    # ----------------------------------------------------------------- models
    @router.get("/models")
    def get_models() -> JSONResponse:
        """Report which ASR model families are installed."""
        from media_processing.asr_models import (
            check_canary_available,
            check_parakeet_available,
            list_model_candidates,
        )
        candidates = list_model_candidates()
        return JSONResponse(content={
            "candidates": candidates,
            "faster_whisper_available": _check_faster_whisper(),
            "parakeet_available": check_parakeet_available(),
            "canary_available": check_canary_available(),
            "nemo_installed": _check_nemo(),
            "cuda_available": _check_cuda(),
        })

    # ----------------------------------------------------------------- status
    @router.get("/status")
    def asr_status() -> JSONResponse:
        default = default_store.get()
        return JSONResponse(content={
            "running": benchmark_service.is_running(),
            "default_preset_id": default["preset_id"],
            "default_preset": default["preset"],
            "default_selected_at": default.get("selected_at"),
        })

    # ----------------------------------------------------------------- default
    @router.get("/default")
    def get_default() -> JSONResponse:
        return JSONResponse(content=default_store.get())

    @router.post("/default")
    def set_default(request: SelectDefaultRequest) -> JSONResponse:
        try:
            payload = default_store.select(request.preset_id)
        except Exception as exc:
            return _map(exc)
        return JSONResponse(content=payload)

    # ----------------------------------------------------------------- audio diagnostics
    @router.get("/audio-diagnostics/{source_type}/{source_id}")
    def list_audio_diagnostics(source_type: str, source_id: str) -> JSONResponse:
        if forensics_service is None:
            return _err(503, "forensics_unavailable", "Audio forensics service not configured.")
        diags = forensics_service.list_diagnostics()
        filtered = [d for d in diags if d.get("source_type") == source_type and d.get("source_id") == source_id]
        return JSONResponse(content={"diagnostics": filtered})

    @router.post("/audio-diagnostics")
    def create_audio_diagnostic(request: CreateAudioDiagnosticRequest) -> JSONResponse:
        if forensics_service is None:
            return _err(503, "forensics_unavailable", "Audio forensics service not configured.")
        try:
            rec = forensics_service.create_diagnostic(
                source_type=request.source_type,
                source_id=request.source_id,
                audio_stream_id=request.audio_stream_id,
            )
        except FileNotFoundError as exc:
            return _err(404, "source_not_found", str(exc))
        except ValueError as exc:
            return _err(400, "invalid_stream", str(exc))
        except Exception as exc:
            logger.exception("audio diagnostic failed")
            return _err(500, "forensics_error", str(exc))
        return JSONResponse(status_code=201, content=rec)

    @router.get("/audio-diagnostics/{diagnostic_id}/artifacts/{variant}")
    def get_audio_artifact(diagnostic_id: str, variant: str) -> Any:
        if forensics_service is None:
            return _err(503, "forensics_unavailable", "Audio forensics service not configured.")
        if variant not in AUDIO_VARIANTS:
            return _err(400, "invalid_variant", f"invalid audio variant: {variant!r}")
        try:
            path = forensics_service.artifact_path(diagnostic_id, variant)
        except ValueError as exc:
            return _err(400, "invalid_id", str(exc))
        if not path.is_file():
            return _err(404, "artifact_not_found", f"artifact not found: {variant}")
        return FileResponse(str(path), media_type="audio/flac", filename=f"{variant}.flac")

    # ----------------------------------------------------------------- benchmarks
    @router.post("/benchmarks")
    def create_benchmark(request: CreateBenchmarkRequest) -> JSONResponse:
        try:
            rec = benchmark_service.create_benchmark(
                source_type=request.source_type,
                source_id=request.source_id,
                preset_ids=request.preset_ids,
                candidate_ids=request.candidate_ids,
                audio_variant=request.audio_variant,
                reference_text=request.reference_text,
                hotwords=request.hotwords,
            )
        except Exception as exc:
            return _map(exc)
        return JSONResponse(status_code=201, content=rec)

    @router.get("/benchmarks")
    def list_benchmarks() -> JSONResponse:
        return JSONResponse(content={"benchmarks": benchmark_service.list_benchmarks()})

    @router.get("/benchmarks/{benchmark_id}")
    def get_benchmark(benchmark_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=benchmark_service.get_benchmark(benchmark_id))
        except Exception as exc:
            return _map(exc)

    @router.post("/benchmarks/{benchmark_id}/start")
    def start_benchmark(benchmark_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=benchmark_service.start(benchmark_id))
        except Exception as exc:
            return _map(exc)

    @router.post("/benchmarks/{benchmark_id}/cancel")
    def cancel_benchmark(benchmark_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=benchmark_service.cancel(benchmark_id))
        except Exception as exc:
            return _map(exc)

    @router.delete("/benchmarks/{benchmark_id}")
    def delete_benchmark(benchmark_id: str) -> JSONResponse:
        try:
            benchmark_service.delete(benchmark_id)
            return JSONResponse(content={"id": benchmark_id, "deleted": True})
        except Exception as exc:
            return _map(exc)

    @router.post("/benchmarks/{benchmark_id}/select-default")
    def select_default_from_benchmark(benchmark_id: str, request: SelectDefaultRequest) -> JSONResponse:
        """Select a production default preset.

        Refuses the diagnostic no-VAD preset and any unknown preset. The
        benchmark id is accepted for provenance logging but the selection
        only depends on the preset id.
        """
        # Verify the benchmark exists (provenance).
        try:
            benchmark_service.get_benchmark(benchmark_id)
        except Exception as exc:
            return _map(exc)
        if not is_production_eligible(request.preset_id):
            return _err(
                400, "preset_not_eligible",
                f"preset {request.preset_id!r} is not eligible as production default.",
            )
        try:
            payload = default_store.select(request.preset_id)
        except Exception as exc:
            return _map(exc)
        return JSONResponse(content=payload)

    # ----------------------------------------------------------------- run detail
    @router.get("/benchmarks/{benchmark_id}/runs/{preset_id}")
    def get_run(benchmark_id: str, preset_id: str) -> JSONResponse:
        try:
            payload = benchmark_service.get_run(benchmark_id, preset_id)
        except Exception as exc:
            return _map(exc)
        if payload is None:
            return _err(404, "run_not_found", f"run not found: {preset_id}")
        return JSONResponse(content=payload)

    return router


# ---------------------------------------------------------------------------
# Runtime availability checks (lazy, no model loading at startup)
# ---------------------------------------------------------------------------


def _check_faster_whisper() -> bool:
    try:
        import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def _check_nemo() -> bool:
    try:
        import nemo  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def _check_cuda() -> bool:
    try:
        import torch  # type: ignore[import-not-found]
        return bool(torch.cuda.is_available())
    except Exception:
        return False
