"""ASR benchmark worker subprocess.

Launched by :class:`media_processing.asr_benchmark.AsrBenchmarkService`
as ``python -m media_processing.asr_benchmark_worker <worker_job.json>``.

The worker:

1. reads the worker job (benchmark id, candidate ids, source, ...);
2. for each candidate, **sequentially**:
   a. acquires the project-wide GPU lock;
   b. loads the model via the appropriate adapter (only when the model
      id changes from the previous run — same-model runs reuse the
      loaded model and are marked ``model_reused=true``);
   c. transcribes the audio with the candidate's exact parameters;
   d. measures VRAM via NVML (backend-independent, not torch-only);
   e. computes VAD diagnosis, WER/CER metrics, hallucination and
      missing-speech flags;
   f. writes the run JSON atomically;
   g. updates the benchmark record with the run summary;
   h. releases the GPU lock;
3. clears CUDA cache between different model families;
4. exits.

Cancel: the parent sets a cancel flag and terminates the process. The
benchmark service marks the benchmark CANCELED in its reaper.
"""

from __future__ import annotations

import datetime as _dt
import gc
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .asr_benchmark import (
    SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    _atomic_write_json,
    _read_json,
    finalise_run,
)
from .asr_models import (
    AsrAdapterError,
    NormalizedTranscriptionResult,
    VramTracker,
    check_candidate_available,
    get_adapter,
    get_candidate,
    measure_peak_ram,
)

logger = logging.getLogger("ttvturbo.media_processing.asr_benchmark_worker")


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _update_benchmark(benchmark_path: Path, run_summary: dict[str, Any]) -> None:
    payload = _read_json(benchmark_path)
    if payload is None:
        return
    runs = payload.get("runs") or []
    # Replace any existing run for the same candidate id.
    runs = [r for r in runs if r.get("candidate_id") != run_summary.get("candidate_id")
            and r.get("preset_id") != run_summary.get("candidate_id")]
    runs.append(run_summary)
    payload["runs"] = runs
    _atomic_write_json(benchmark_path, payload)


def _resolve_audio_path(source_type: str, source_id: str) -> Path:
    """Resolve the ready audio artifact path for the source."""
    from ttvturbo.media_processing.audio_extraction import AudioExtractionService  # noqa: PLC0415
    from ttvturbo.media_processing.sources import MediaSourceResolver  # noqa: PLC0415
    from ttvturbo.media_processing.storage import MediaJobStorage  # noqa: PLC0415
    from ttvturbo.vod_pipeline import VodPipelineStorage  # noqa: PLC0415

    data_dir = Path(os.environ.get("TTVTURBO_DATA_DIR") or
                    (Path(__file__).resolve().parents[2] / "data"))
    vod_storage = VodPipelineStorage(data_dir)
    from ttvturbo.library import LibraryService, LibraryStorage  # noqa: PLC0415
    library_service = LibraryService(LibraryStorage(data_dir / "library"))
    from ttvturbo.media_processing.uploads import UploadStorage  # noqa: PLC0415
    upload_storage = UploadStorage(data_dir / "uploads")
    resolver = MediaSourceResolver(
        vod_storage,
        upload_storage=upload_storage,
        library_service=library_service,
    )
    storage = MediaJobStorage(data_dir)
    audio_service = AudioExtractionService(storage=storage, source_resolver=resolver)
    meta = audio_service.get_audio_artifact(source_id, source_type)
    if meta is None:
        raise RuntimeError(
            f"no ready audio artifact for source_type={source_type} source_id={source_id}; "
            "extract audio first"
        )
    path = audio_service.artifact_path(source_id, source_type)
    if not path.is_file():
        raise RuntimeError(f"audio artifact file missing on disk: {path}")
    return path


def _resolve_forensic_audio(
    source_type: str, source_id: str, audio_variant: str
) -> Path:
    """Resolve a forensic audio variant artifact as the ASR input."""
    from ttvturbo.media_processing.audio_forensics import AudioForensicsService  # noqa: PLC0415
    from ttvturbo.media_processing.sources import MediaSourceResolver  # noqa: PLC0415
    from ttvturbo.vod_pipeline import VodPipelineStorage  # noqa: PLC0415

    data_dir = Path(os.environ.get("TTVTURBO_DATA_DIR") or
                    (Path(__file__).resolve().parents[2] / "data"))
    vod_storage = VodPipelineStorage(data_dir)
    from ttvturbo.library import LibraryService, LibraryStorage  # noqa: PLC0415
    library_service = LibraryService(LibraryStorage(data_dir / "library"))
    from ttvturbo.media_processing.uploads import UploadStorage  # noqa: PLC0415
    upload_storage = UploadStorage(data_dir / "uploads")
    resolver = MediaSourceResolver(
        vod_storage,
        upload_storage=upload_storage,
        library_service=library_service,
    )
    svc = AudioForensicsService(data_dir, resolver)
    # Find the most recent diagnostic for this source.
    diags = svc.list_diagnostics()
    matching = [d for d in diags if d.get("source_type") == source_type and d.get("source_id") == source_id]
    if not matching:
        raise RuntimeError(
            f"no audio diagnostic found for source_type={source_type} source_id={source_id}; "
            "create a diagnostic first"
        )
    diag_id = matching[0]["id"]
    path = svc.artifact_path(diag_id, audio_variant)
    if not path.is_file():
        raise RuntimeError(f"forensic audio variant {audio_variant!r} not found: {path}")
    return path


def _clear_cuda_cache() -> None:
    try:
        import torch  # type: ignore[import-not-found]
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _framework_version(model_family: str) -> Optional[str]:
    if model_family == "whisper":
        try:
            import faster_whisper  # type: ignore[import-not-found]
            return getattr(faster_whisper, "__version__", None)
        except Exception:
            return None
    if model_family in ("parakeet", "canary"):
        try:
            import nemo  # type: ignore[import-not-found]
            return getattr(nemo, "__version__", None)
        except Exception:
            return None
    return None


def run_worker(worker_job_path: str) -> int:
    try:
        with open(worker_job_path, "r", encoding="utf-8") as fh:
            wjob = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("could not read worker job %s: %s", worker_job_path, exc)
        return 2

    benchmark_path = Path(wjob["benchmark_path"])
    runs_dir = Path(wjob["runs_dir"])
    runs_dir.mkdir(parents=True, exist_ok=True)
    source_type = wjob["source_type"]
    source_id = wjob["source_id"]
    candidate_ids = wjob.get("candidate_ids") or wjob.get("preset_ids") or []
    reference_text = wjob.get("reference_text")
    hotwords = wjob.get("hotwords")
    gpu_lock_dir = Path(wjob["gpu_lock_dir"])
    audio_variant = wjob.get("audio_variant")  # optional forensic variant

    payload = _read_json(benchmark_path)
    if payload is None:
        logger.error("benchmark file missing: %s", benchmark_path)
        return 2

    # Resolve audio path — either a forensic variant or the standard artifact.
    try:
        if audio_variant and audio_variant != "current-asr-input":
            audio_path = _resolve_forensic_audio(source_type, source_id, audio_variant)
        else:
            audio_path = _resolve_audio_path(source_type, source_id)
    except Exception as exc:
        for cid in candidate_ids:
            _write_failed_run(runs_dir, benchmark_path, cid, str(exc), candidate=None)
        return 1

    from ttvturbo.media_processing.gpu_lock import GpuLock, GpuLockOwner  # noqa: PLC0415

    gpu_lock = GpuLock(gpu_lock_dir)

    try:
        with GpuLockOwner(gpu_lock, owner_type="transcription",
                          job_id=f"asr-benchmark-{payload['id']}",
                          timeout_seconds=0.0):
            # Track which model is loaded so we can reuse it.
            loaded_family: Optional[str] = None
            loaded_model_id: Optional[str] = None
            current_adapter = None
            vram_tracker = VramTracker(gpu_index=0)
            vram_tracker.init()
            try:
                for cid in candidate_ids:
                    candidate = get_candidate(cid)
                    if candidate is None:
                        _write_failed_run(
                            runs_dir, benchmark_path, cid,
                            f"unknown candidate id: {cid}", candidate=None,
                        )
                        continue

                    # Check availability — skipped, not failed, if not installed.
                    if not check_candidate_available(cid):
                        _write_skipped_run(
                            runs_dir, benchmark_path, cid,
                            f"model family {candidate.model_family!r} not installed",
                            candidate=candidate,
                        )
                        continue

                    # Load model if needed (different family or model id).
                    model_reused = (
                        current_adapter is not None
                        and loaded_family == candidate.model_family
                        and loaded_model_id == candidate.model_id
                    )
                    if not model_reused:
                        # Release previous model.
                        if current_adapter is not None:
                            current_adapter.release()
                            current_adapter = None
                            loaded_family = None
                            loaded_model_id = None
                            gc.collect()
                            _clear_cuda_cache()

                    # Get adapter (reuse if same model).
                    if current_adapter is None or not model_reused:
                        try:
                            current_adapter = get_adapter(candidate.model_family)
                        except AsrAdapterError as exc:
                            _write_failed_run(
                                runs_dir, benchmark_path, cid,
                                str(exc), candidate=candidate,
                            )
                            continue

                    # Transcribe.
                    try:
                        # Merge candidate options with model_id.
                        opts = dict(candidate.options)
                        opts["model_id"] = candidate.model_id
                        if hotwords:
                            opts["hotwords"] = hotwords
                        result: NormalizedTranscriptionResult = current_adapter.transcribe(
                            str(audio_path), opts, vram_tracker=vram_tracker,
                        )
                    except AsrAdapterError as exc:
                        _write_failed_run(
                            runs_dir, benchmark_path, cid,
                            str(exc), candidate=candidate,
                        )
                        _clear_cuda_cache()
                        continue
                    except Exception as exc:
                        _write_failed_run(
                            runs_dir, benchmark_path, cid,
                            f"unexpected error: {type(exc).__name__}: {exc}",
                            candidate=candidate,
                        )
                        _clear_cuda_cache()
                        continue

                    # Measure VRAM after release.
                    vram_tracker.measure_after_release()

                    run_payload = {
                        "schema_version": SCHEMA_VERSION,
                        "candidate_id": cid,
                        "preset_id": cid,  # backward compat
                        "candidate": candidate.to_dict(),
                        "preset": candidate.to_dict(),  # backward compat
                        "status": STATUS_READY,
                        "framework": candidate.model_family,
                        "framework_version": _framework_version(candidate.model_family),
                        "model_family": result.model_family,
                        "model_id": result.model_id,
                        "model_revision": result.model_revision,
                        "model_reused": result.model_reused,
                        "load_seconds": result.load_seconds,
                        "inference_seconds": result.inference_seconds,
                        "total_seconds": result.total_seconds,
                        "runtime_seconds": result.inference_seconds,  # backward compat
                        "model_load_seconds": result.load_seconds,  # backward compat
                        "peak_vram_bytes": result.peak_vram_bytes,
                        "peak_vram_mb": (
                            round(result.peak_vram_bytes / (1024 * 1024), 3)
                            if result.peak_vram_bytes is not None else None
                        ),
                        "vram": vram_tracker.to_dict(),
                        "peak_ram_bytes": result.peak_ram_bytes,
                        "audio_variant": audio_variant or "current-asr-input",
                        "audio_duration_seconds": None,
                        "detected_language": result.language,
                        "language_probability": result.language_probability,
                        "all_language_probs": None,
                        "duration_after_vad_from_info": None,
                        "transcript_text": result.text,
                        "segments": result.segments,
                        "words": result.words,
                        "effective_parameters": candidate.options,
                        "hotwords_used": hotwords or None,
                        "warnings": result.warnings,
                        "created_at": _now_iso(),
                    }

                    # Attach VAD diagnosis, metrics, flags.
                    # For VAD diagnosis we need a preset-like object.
                    from .asr_presets import AsrPreset  # noqa: PLC0415
                    vad_preset = AsrPreset(
                        id=cid,
                        name=candidate.name,
                        description=candidate.description,
                        model=candidate.model_id,
                        device="cuda",
                        compute_type=candidate.options.get("compute_type", "int8_float16"),
                        task="transcribe",
                        language=candidate.options.get("language"),
                        multilingual=candidate.options.get("multilingual", False),
                        beam_size=candidate.options.get("beam_size", 5),
                        word_timestamps=candidate.options.get("word_timestamps", True),
                        vad_filter=candidate.options.get("vad_filter", True),
                        condition_on_previous_text=candidate.options.get(
                            "condition_on_previous_text", True
                        ),
                    )
                    run_payload = finalise_run(
                        run_payload,
                        str(audio_path),
                        vad_preset,
                        reference_text,
                        compute_vad=candidate.options.get("vad_filter", True),
                    )
                    _atomic_write_json(runs_dir / f"{cid}.json", run_payload)
                    _update_benchmark(benchmark_path, _summarise_run(run_payload))

                    # Update loaded model tracking.
                    loaded_family = candidate.model_family
                    loaded_model_id = candidate.model_id
            finally:
                if current_adapter is not None:
                    try:
                        current_adapter.release()
                    except Exception:
                        pass
                gc.collect()
                _clear_cuda_cache()
                vram_tracker.measure_after_release()
                vram_tracker.shutdown()
    except Exception as exc:
        for cid in candidate_ids:
            existing = _read_json(runs_dir / f"{cid}.json")
            if existing is None or existing.get("status") != STATUS_READY:
                _write_failed_run(
                    runs_dir, benchmark_path, cid,
                    f"benchmark worker error: {type(exc).__name__}: {exc}",
                    candidate=None,
                )
        return 1
    return 0


def _summarise_run(run_payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact run summary for the benchmark record's ``runs`` list."""
    metrics = run_payload.get("metrics") or {}
    return {
        "candidate_id": run_payload.get("candidate_id"),
        "preset_id": run_payload.get("candidate_id"),  # backward compat
        "preset_name": (run_payload.get("candidate") or {}).get("name"),
        "model": (run_payload.get("candidate") or {}).get("model_id"),
        "model_family": run_payload.get("model_family"),
        "status": run_payload.get("status"),
        "runtime_seconds": run_payload.get("runtime_seconds"),
        "inference_seconds": run_payload.get("inference_seconds"),
        "model_load_seconds": run_payload.get("model_load_seconds"),
        "model_reused": run_payload.get("model_reused"),
        "load_seconds": run_payload.get("load_seconds"),
        "total_seconds": run_payload.get("total_seconds"),
        "peak_vram_bytes": run_payload.get("peak_vram_bytes"),
        "peak_vram_mb": run_payload.get("peak_vram_mb"),
        "peak_ram_bytes": run_payload.get("peak_ram_bytes"),
        "audio_variant": run_payload.get("audio_variant"),
        "detected_language": run_payload.get("detected_language"),
        "language_probability": run_payload.get("language_probability"),
        "audio_duration_seconds": run_payload.get("audio_duration_seconds"),
        "wer": metrics.get("wer"),
        "cer": metrics.get("cer"),
        "substitutions": metrics.get("substitutions"),
        "deletions": metrics.get("deletions"),
        "insertions": metrics.get("insertions"),
        "metrics_available": metrics.get("available"),
        "hallucination_flag_count": len(run_payload.get("hallucination_flags") or []),
        "missing_speech_flag_count": len(run_payload.get("missing_speech_flags") or []),
        "transcript_text": run_payload.get("transcript_text"),
        "error": run_payload.get("error"),
        "skip_reason": run_payload.get("skip_reason"),
        "warnings": run_payload.get("warnings"),
    }


def _write_failed_run(
    runs_dir: Path,
    benchmark_path: Path,
    candidate_id: str,
    error: str,
    candidate: Optional[Any] = None,
) -> None:
    from .asr_models import ModelCandidate  # noqa: PLC0415
    cand_dict = candidate.to_dict() if candidate is not None else None
    run_payload = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "preset_id": candidate_id,  # backward compat
        "candidate": cand_dict,
        "preset": cand_dict,  # backward compat
        "status": STATUS_FAILED,
        "error": error,
        "framework": candidate.model_family if candidate else None,
        "framework_version": None,
        "model_family": candidate.model_family if candidate else None,
        "model_id": candidate.model_id if candidate else None,
        "model_reused": False,
        "load_seconds": None,
        "inference_seconds": None,
        "total_seconds": None,
        "runtime_seconds": None,
        "model_load_seconds": None,
        "peak_vram_bytes": None,
        "peak_vram_mb": None,
        "vram": None,
        "peak_ram_bytes": None,
        "audio_variant": None,
        "audio_duration_seconds": None,
        "detected_language": None,
        "language_probability": None,
        "all_language_probs": None,
        "duration_after_vad_from_info": None,
        "transcript_text": "",
        "segments": [],
        "words": [],
        "effective_parameters": candidate.options if candidate else {},
        "hotwords_used": None,
        "warnings": [],
        "metrics": {"available": False},
        "hallucination_flags": [],
        "missing_speech_flags": [],
        "vad_diagnosis": None,
        "created_at": _now_iso(),
    }
    _atomic_write_json(runs_dir / f"{candidate_id}.json", run_payload)
    _update_benchmark(benchmark_path, _summarise_run(run_payload))


def _write_skipped_run(
    runs_dir: Path,
    benchmark_path: Path,
    candidate_id: str,
    reason: str,
    candidate: Optional[Any] = None,
) -> None:
    """Write a SKIPPED run — the model family is not installed.

    Distinct from FAILED: the run was never attempted, so it should not
    count against the benchmark's success/failure tally.
    """
    cand_dict = candidate.to_dict() if candidate is not None else None
    run_payload = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "preset_id": candidate_id,  # backward compat
        "candidate": cand_dict,
        "preset": cand_dict,  # backward compat
        "status": STATUS_SKIPPED,
        "error": None,  # not an error — just unavailable
        "skip_reason": reason,
        "framework": candidate.model_family if candidate else None,
        "framework_version": None,
        "model_family": candidate.model_family if candidate else None,
        "model_id": candidate.model_id if candidate else None,
        "model_reused": False,
        "load_seconds": None,
        "inference_seconds": None,
        "total_seconds": None,
        "runtime_seconds": None,
        "model_load_seconds": None,
        "peak_vram_bytes": None,
        "peak_vram_mb": None,
        "vram": None,
        "peak_ram_bytes": None,
        "audio_variant": None,
        "audio_duration_seconds": None,
        "detected_language": None,
        "language_probability": None,
        "all_language_probs": None,
        "duration_after_vad_from_info": None,
        "transcript_text": "",
        "segments": [],
        "words": [],
        "effective_parameters": candidate.options if candidate else {},
        "hotwords_used": None,
        "warnings": [],
        "metrics": {"available": False},
        "hallucination_flags": [],
        "missing_speech_flags": [],
        "vad_diagnosis": None,
        "created_at": _now_iso(),
    }
    _atomic_write_json(runs_dir / f"{candidate_id}.json", run_payload)
    _update_benchmark(benchmark_path, _summarise_run(run_payload))


if __name__ == "__main__":
    sys.exit(run_worker(sys.argv[1]))
