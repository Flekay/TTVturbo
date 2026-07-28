"""ASR benchmark worker subprocess.

Launched by :class:`media_processing.asr_benchmark.AsrBenchmarkService`
as ``python -m media_processing.asr_benchmark_worker <worker_job.json>``.

The worker:

1. reads the worker job (benchmark id, preset ids, source, hotwords, ...);
2. for each preset, **sequentially**:
   a. checks faster-whisper compatibility for the preset;
   b. acquires the project-wide GPU lock;
   c. loads the model (only when the model id changes from the previous
      run — Large v3 and Large v3 Turbo are never loaded at the same
      time, but two consecutive Large v3 runs reuse the loaded model);
   d. transcribes the audio with the preset's exact parameters;
   e. computes VAD diagnosis, WER/CER metrics, hallucination and
      missing-speech flags;
   f. writes the run JSON atomically;
   g. updates the benchmark record with the run summary;
   h. releases the GPU lock;
3. clears CUDA cache between different models;
4. exits.

Cancel: the parent sets a cancel flag and terminates the process. The
benchmark service marks the benchmark CANCELED in its reaper.
"""

from __future__ import annotations

import datetime as _dt
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
    _atomic_write_json,
    _read_json,
    finalise_run,
)
from .asr_presets import (
    AsrPreset,
    check_preset_compatibility,
    get_preset,
)

logger = logging.getLogger("ttvturbo.media_processing.asr_benchmark_worker")


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _update_benchmark(benchmark_path: Path, run_summary: dict[str, Any]) -> None:
    payload = _read_json(benchmark_path)
    if payload is None:
        return
    runs = payload.get("runs") or []
    # Replace any existing run for the same preset id.
    runs = [r for r in runs if r.get("preset_id") != run_summary.get("preset_id")]
    runs.append(run_summary)
    payload["runs"] = runs
    _atomic_write_json(benchmark_path, payload)


def _resolve_audio_path(source_type: str, source_id: str) -> Path:
    """Resolve the ready audio artifact path for the source.

    The benchmark requires a ready audio artifact (FLAC) produced by the
    audio extraction service. If none exists the worker fails the
    benchmark up-front rather than per-preset.
    """
    from media_processing.audio_extraction import AudioExtractionService  # noqa: PLC0415
    from media_processing.sources import MediaSourceResolver  # noqa: PLC0415
    from media_processing.storage import MediaJobStorage  # noqa: PLC0415
    from vod_pipeline import VodPipelineStorage  # noqa: PLC0415

    # We cannot reach the app.py singletons from the subprocess; rebuild
    # the minimal resolver/storage needed to locate the audio artifact.
    data_dir = Path(os.environ.get("TTVTURBO_DATA_DIR") or
                    (Path(__file__).resolve().parents[1] / "data"))
    vod_storage = VodPipelineStorage(data_dir)
    from library import LibraryService, LibraryStorage  # noqa: PLC0415
    library_service = LibraryService(LibraryStorage(data_dir / "library"))
    from media_processing.uploads import UploadStorage  # noqa: PLC0415
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


def _transcribe_one(
    model,
    audio_path: Path,
    preset: AsrPreset,
    hotwords: Optional[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one transcription. Returns (segments, info_dict)."""
    kw = preset.transcribe_kwargs()
    # Apply per-job hotwords (override preset hotwords).
    if hotwords:
        kw["hotwords"] = hotwords
    elif "hotwords" in kw:
        # Don't forward the preset's empty hotwords string.
        kw.pop("hotwords", None)

    segments_iter, info = model.transcribe(str(audio_path), **kw)
    segments: list[dict[str, Any]] = []
    for seg in segments_iter:
        words: list[dict[str, Any]] = []
        for w in (getattr(seg, "words", None) or []):
            words.append({
                "start": float(getattr(w, "start", 0.0)),
                "end": float(getattr(w, "end", 0.0)),
                "text": str(getattr(w, "word", "")),
                "probability": float(getattr(w, "probability", 0.0)) or None,
            })
        segments.append({
            "id": int(getattr(seg, "id", len(segments))),
            "start": float(getattr(seg, "start", 0.0)),
            "end": float(getattr(seg, "end", 0.0)),
            "text": str(getattr(seg, "text", "")).strip(),
            "avg_logprob": float(getattr(seg, "avg_logprob", 0.0)) or None,
            "compression_ratio": float(getattr(seg, "compression_ratio", 0.0)) or None,
            "no_speech_probability": float(getattr(seg, "no_speech_prob", 0.0)) or None,
            "words": words,
        })
    info_dict = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "duration_after_vad": getattr(info, "duration_after_vad", None),
        "all_language_probs": getattr(info, "all_language_probs", None),
    }
    return segments, info_dict


def _load_model(model_name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    fw_device = "cuda" if device.startswith("cuda") else "cpu"
    return WhisperModel(model_name, device=fw_device, compute_type=compute_type)


def _peak_vram_mb() -> Optional[float]:
    try:
        import torch  # type: ignore[import-not-found]
        if torch.cuda.is_available():
            return float(torch.cuda.max_memory_allocated() / (1024 * 1024))
    except Exception:
        return None
    return None


def _reset_vram_peak() -> None:
    try:
        import torch  # type: ignore[import-not-found]
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def _clear_cuda_cache() -> None:
    try:
        import torch  # type: ignore[import-not-found]
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _faster_whisper_version() -> Optional[str]:
    try:
        import faster_whisper  # type: ignore[import-not-found]
        return getattr(faster_whisper, "__version__", None)
    except Exception:
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
    preset_ids = wjob["preset_ids"]
    reference_text = wjob.get("reference_text")
    hotwords = wjob.get("hotwords")
    gpu_lock_dir = Path(wjob["gpu_lock_dir"])

    payload = _read_json(benchmark_path)
    if payload is None:
        logger.error("benchmark file missing: %s", benchmark_path)
        return 2

    try:
        audio_path = _resolve_audio_path(source_type, source_id)
    except Exception as exc:
        # Mark every selected preset failed.
        for pid in preset_ids:
            _write_failed_run(runs_dir, benchmark_path, pid, str(exc), preset=None)
        return 1

    from media_processing.gpu_lock import GpuLock, GpuLockOwner  # noqa: PLC0415

    gpu_lock = GpuLock(gpu_lock_dir)

    fw_version = _faster_whisper_version()

    try:
        with GpuLockOwner(gpu_lock, owner_type="transcription",
                          job_id=f"asr-benchmark-{payload['id']}",
                          timeout_seconds=0.0):
            loaded_model_name: Optional[str] = None
            loaded_model = None
            try:
                for pid in preset_ids:
                    preset = get_preset(pid)
                    # Compatibility check: refuse unknown params.
                    reasons = check_preset_compatibility(preset)
                    if reasons:
                        _write_failed_run(
                            runs_dir, benchmark_path, pid,
                            "preset incompatible with installed faster-whisper: "
                            + "; ".join(reasons),
                            preset=preset, fw_version=fw_version,
                        )
                        continue

                    # Load model if needed.
                    if loaded_model is None or loaded_model_name != preset.model:
                        if loaded_model is not None:
                            del loaded_model
                            loaded_model = None
                            _clear_cuda_cache()
                        _reset_vram_peak()
                        load_start = time.monotonic()
                        try:
                            loaded_model = _load_model(
                                preset.model, preset.device, preset.compute_type
                            )
                            loaded_model_name = preset.model
                        except Exception as exc:
                            _write_failed_run(
                                runs_dir, benchmark_path, pid,
                                f"could not load model {preset.model}: "
                                f"{type(exc).__name__}: {exc}",
                                preset=preset, fw_version=fw_version,
                            )
                            continue
                        model_load_seconds = time.monotonic() - load_start
                    else:
                        model_load_seconds = 0.0
                        _reset_vram_peak()

                    # Transcribe.
                    t_start = time.monotonic()
                    try:
                        segments, info_dict = _transcribe_one(
                            loaded_model, audio_path, preset, hotwords
                        )
                    except Exception as exc:
                        _write_failed_run(
                            runs_dir, benchmark_path, pid,
                            f"transcription failed: {type(exc).__name__}: {exc}",
                            preset=preset, fw_version=fw_version,
                            model_load_seconds=model_load_seconds,
                        )
                        _clear_cuda_cache()
                        continue
                    runtime_seconds = time.monotonic() - t_start
                    peak_vram_mb = _peak_vram_mb()

                    run_payload = {
                        "schema_version": SCHEMA_VERSION,
                        "preset_id": pid,
                        "preset": preset.to_dict(),
                        "status": STATUS_READY,
                        "faster_whisper_version": fw_version,
                        "model_load_seconds": round(model_load_seconds, 3),
                        "runtime_seconds": round(runtime_seconds, 3),
                        "peak_vram_mb": round(peak_vram_mb, 3) if peak_vram_mb is not None else None,
                        "audio_duration_seconds": float(info_dict.get("duration") or 0.0) or None,
                        "detected_language": info_dict.get("language"),
                        "language_probability": info_dict.get("language_probability"),
                        "all_language_probs": info_dict.get("all_language_probs"),
                        "duration_after_vad_from_info": info_dict.get("duration_after_vad"),
                        "transcript_text": " ".join(s.get("text", "") for s in segments).strip(),
                        "segments": segments,
                        "effective_parameters": preset.transcribe_kwargs(),
                        "hotwords_used": hotwords or None,
                        "created_at": _now_iso(),
                    }
                    # Attach VAD diagnosis, metrics, flags.
                    run_payload = finalise_run(
                        run_payload,
                        str(audio_path),
                        preset,
                        reference_text,
                        compute_vad=preset.vad_filter,
                    )
                    _atomic_write_json(runs_dir / f"{pid}.json", run_payload)
                    _update_benchmark(benchmark_path, _summarise_run(run_payload))
            finally:
                if loaded_model is not None:
                    try:
                        del loaded_model
                    except Exception:
                        pass
                _clear_cuda_cache()
    except Exception as exc:
        # GPU lock busy or other outer failure: mark remaining presets failed.
        for pid in preset_ids:
            existing = _read_json(runs_dir / f"{pid}.json")
            if existing is None or existing.get("status") != STATUS_READY:
                _write_failed_run(
                    runs_dir, benchmark_path, pid,
                    f"benchmark worker error: {type(exc).__name__}: {exc}",
                    preset=None, fw_version=fw_version,
                )
        return 1
    return 0


def _summarise_run(run_payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact run summary for the benchmark record's ``runs`` list."""
    metrics = run_payload.get("metrics") or {}
    return {
        "preset_id": run_payload.get("preset_id"),
        "preset_name": (run_payload.get("preset") or {}).get("name"),
        "model": (run_payload.get("preset") or {}).get("model"),
        "status": run_payload.get("status"),
        "runtime_seconds": run_payload.get("runtime_seconds"),
        "model_load_seconds": run_payload.get("model_load_seconds"),
        "peak_vram_mb": run_payload.get("peak_vram_mb"),
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
    }


def _write_failed_run(
    runs_dir: Path,
    benchmark_path: Path,
    preset_id: str,
    error: str,
    preset: Optional[AsrPreset],
    fw_version: Optional[str] = None,
    model_load_seconds: Optional[float] = None,
) -> None:
    run_payload = {
        "schema_version": SCHEMA_VERSION,
        "preset_id": preset_id,
        "preset": preset.to_dict() if preset is not None else None,
        "status": STATUS_FAILED,
        "error": error,
        "faster_whisper_version": fw_version,
        "model_load_seconds": round(model_load_seconds, 3) if model_load_seconds is not None else None,
        "runtime_seconds": None,
        "peak_vram_mb": None,
        "audio_duration_seconds": None,
        "detected_language": None,
        "language_probability": None,
        "all_language_probs": None,
        "duration_after_vad_from_info": None,
        "transcript_text": "",
        "segments": [],
        "effective_parameters": preset.transcribe_kwargs() if preset is not None else {},
        "hotwords_used": None,
        "metrics": {"available": False, "error": "run failed before metrics"},
        "vad_diagnosis": {"computed": False, "audio_duration_seconds": None,
                          "duration_after_vad_seconds": None,
                          "removed_by_vad_seconds": None, "speech_regions": []},
        "hallucination_flags": [],
        "missing_speech_flags": [],
        "created_at": _now_iso(),
    }
    _atomic_write_json(runs_dir / f"{preset_id}.json", run_payload)
    _update_benchmark(benchmark_path, _summarise_run(run_payload))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m media_processing.asr_benchmark_worker <worker_job.json>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run_worker(sys.argv[1])
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("asr benchmark worker crashed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
