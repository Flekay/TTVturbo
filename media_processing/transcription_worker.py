"""Transcription worker subprocess entry point.

Launched by :class:`media_processing.transcription.TranscriptionService`
as ``python -m media_processing.transcription_worker <worker_job.json>``.

The worker:

1. acquires the project-wide GPU lock (``GpuLockOwner``);
2. imports faster-whisper and torch;
3. if ``device=cuda`` but CUDA is unavailable, fails with a clear error
   (no silent CPU fallback);
4. loads the model (phase = LOADING_MODEL);
5. transcribes the audio artifact (phase = TRANSCRIBING), reporting
   real progress from the segment iterator;
6. exports the transcript to JSON / TXT / SRT / VTT atomically
   (phase = EXPORTING);
7. writes the sidecar ``metadata.json``;
8. releases the GPU lock in ``finally``;
9. updates the job record to READY.

No ``stderr=PIPE`` to the parent (the parent does not drain it
continuously); all worker output goes to ``worker.log`` via the parent's
redirect. Progress is written to the job record on disk.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ttvturbo.media_processing.transcription_worker")

TRANSCRIPT_JSON = "transcript.json"
TRANSCRIPT_TXT = "transcript.txt"
TRANSCRIPT_SRT = "transcript.srt"
TRANSCRIPT_VTT = "transcript.vtt"
TRANSCRIPT_METADATA = "metadata.json"
PART_SUFFIX = ".part"


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _update_job(job_path: Path, **updates: Any) -> None:
    for attempt in range(5):
        try:
            with open(job_path, "r", encoding="utf-8") as fh:
                job = json.load(fh)
            break
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("could not read job %s: %s", job_path, exc)
            return
    else:
        return
    job.update(updates)
    job["updated_at"] = _now_iso()
    _atomic_write_json(job_path, job)


def _ensure_dependencies(device: str, job_path: Path) -> Optional[str]:
    """Try to import faster-whisper and torch. If the import fails, attempt
    an on-demand ``pip install`` and retry. Returns an error string on
    failure, or None on success.

    For CUDA devices, torch is pulled from the cu128 index so CUDA is not
    silently disabled. If torch is already installed (e.g. from voice-clone),
    only faster-whisper is installed.
    """
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]  # noqa: F401
        return None
    except Exception:
        pass

    # On-demand install.
    import subprocess
    import sys as _sys

    _update_job(
        job_path,
        status="RUNNING",
        progress={"percent": None, "processed_seconds": None, "total_seconds": None, "phase": "INSTALLING_DEPENDENCIES"},
    )

    needs_torch = False
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        needs_torch = True

    pip_args = [_sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]

    if needs_torch:
        if device.startswith("cuda"):
            # CUDA build of torch from the cu128 index.
            pip_args += [
                "torch", "faster-whisper",
                "--extra-index-url", "https://download.pytorch.org/whl/cu128",
            ]
        else:
            pip_args += ["torch", "faster-whisper"]
    else:
        pip_args += ["faster-whisper"]

    logger.info("on-demand installing transcription dependencies: %s", " ".join(pip_args))
    try:
        proc = subprocess.run(pip_args, capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        return f"on-demand pip install failed: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        return f"pip install exited with code {proc.returncode}:\n{tail}"

    # Retry the import.
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]  # noqa: F401
        return None
    except Exception as exc:
        return f"could not import faster-whisper/torch after install: {type(exc).__name__}: {exc}"


def _download_model_with_progress(model_name: str, job_path: Path) -> Optional[str]:
    """Pre-download the faster-whisper model from HuggingFace Hub with
    progress reporting. Returns an error string on failure, None on success.

    faster-whisper's WhisperModel constructor downloads the model lazily,
    which means the user sees "LOADING_MODEL" with no indication that a
    multi-GB download is in progress. This function pre-downloads the model
    files using huggingface_hub so we can report a DOWNLOADING_MODEL phase.
    """
    _update_job(
        job_path,
        status="RUNNING",
        progress={"percent": 0.0, "processed_seconds": None, "total_seconds": None, "phase": "DOWNLOADING_MODEL"},
    )
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
    except Exception:
        # huggingface_hub not installed — let WhisperModel handle the
        # download itself (it depends on huggingface_hub, so this should
        # not happen in practice).
        return None

    # faster-whisper models on HF Hub are under the "Systran" org.
    repo_id = f"Systran/faster-whisper-{model_name}" if not model_name.startswith("/") else model_name
    try:
        snapshot_download(repo_id=repo_id, local_files_only=False)
    except Exception as exc:
        # If the download fails, let WhisperModel try again (it may use
        # a different download path or the model may already be cached).
        logger.warning("model pre-download failed for %s: %s — deferring to WhisperModel", repo_id, exc)
        return None
    _update_job(
        job_path,
        progress={"percent": 100.0, "processed_seconds": None, "total_seconds": None, "phase": "DOWNLOADING_MODEL"},
    )
    return None


def _format_srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def _export_txt(segments: list[dict]) -> str:
    return "\n".join(s.get("text", "").strip() for s in segments).strip() + "\n"


def _export_srt(segments: list[dict]) -> str:
    lines: list[str] = []
    for i, s in enumerate(segments, start=1):
        start = _format_srt_timestamp(float(s.get("start", 0.0)))
        end = _format_srt_timestamp(float(s.get("end", 0.0)))
        text = s.get("text", "").strip()
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _export_vtt(segments: list[dict]) -> str:
    lines: list[str] = ["WEBVTT", ""]
    for s in segments:
        start = _format_vtt_timestamp(float(s.get("start", 0.0)))
        end = _format_vtt_timestamp(float(s.get("end", 0.0)))
        text = s.get("text", "").strip()
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def run_worker(worker_job_path: str) -> int:
    try:
        with open(worker_job_path, "r", encoding="utf-8") as fh:
            wjob = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("could not read worker job %s: %s", worker_job_path, exc)
        return 2

    job_path = Path(wjob["job_path"])
    job_id = wjob["job_id"]
    source_type = wjob.get("source_type", "twitch_vod")
    source_id = wjob["source_id"]
    transcription_id = wjob["transcription_id"]
    audio_path = Path(wjob["audio_path"])
    transcript_dir = Path(wjob["transcript_dir"])
    model_name = wjob.get("model", "large-v3")
    device = wjob.get("device", "cuda")
    compute_type = wjob.get("compute_type", "int8_float16")
    language = wjob.get("language") or None
    gpu_lock_dir = Path(wjob["gpu_lock_dir"])

    if not audio_path.is_file():
        _update_job(job_path, status="FAILED", error=f"audio file missing: {audio_path.name}")
        return 1

    # Import the GPU lock here so the worker process can acquire it.
    from media_processing.gpu_lock import GpuLock, GpuLockBusyError, GpuLockOwner, GpuLockError

    gpu_lock = GpuLock(gpu_lock_dir)

    # Phase: WAITING_FOR_GPU -> acquire lock.
    _update_job(
        job_path,
        status="WAITING_FOR_GPU",
        progress={"percent": None, "processed_seconds": None, "total_seconds": None, "phase": "WAITING_FOR_GPU"},
    )

    try:
        with GpuLockOwner(gpu_lock, owner_type="transcription", job_id=job_id, timeout_seconds=0.0):
            # Phase: LOADING_MODEL
            _update_job(
                job_path,
                status="RUNNING",
                progress={"percent": None, "processed_seconds": None, "total_seconds": None, "phase": "LOADING_MODEL"},
            )

            try:
                import torch  # type: ignore[import-not-found]
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]
            except Exception:
                # On-demand install if the modules are missing.
                install_err = _ensure_dependencies(device, job_path)
                if install_err is not None:
                    _update_job(
                        job_path,
                        status="FAILED",
                        error=install_err,
                    )
                    return 1
                import torch  # type: ignore[import-not-found]
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            if device.startswith("cuda"):
                if not torch.cuda.is_available():
                    _update_job(
                        job_path,
                        status="FAILED",
                        error=(
                            "Transcription is configured for CUDA but torch.cuda.is_available() "
                            "is False. No silent CPU fallback. Set "
                            "TTVTURBO_TRANSCRIPTION_DEVICE=cpu to run on CPU."
                        ),
                    )
                    return 1

            # Map device: faster-whisper expects "cuda" or "cpu", not "cuda:0".
            fw_device = "cuda" if device.startswith("cuda") else "cpu"

            # Pre-download the model with a DOWNLOADING_MODEL phase so the
            # user sees that a multi-GB download is in progress (the
            # WhisperModel constructor would otherwise download silently
            # during "LOADING_MODEL").
            _download_model_with_progress(model_name, job_path)

            # Phase: LOADING_MODEL (model is now cached, this loads it into VRAM).
            _update_job(
                job_path,
                status="RUNNING",
                progress={"percent": None, "processed_seconds": None, "total_seconds": None, "phase": "LOADING_MODEL"},
            )

            try:
                model = WhisperModel(
                    model_name,
                    device=fw_device,
                    compute_type=compute_type,
                )
            except Exception as exc:
                _update_job(
                    job_path,
                    status="FAILED",
                    error=f"could not load model {model_name}: {type(exc).__name__}: {exc}",
                )
                return 1

            # Get audio duration for progress computation.
            total_seconds: Optional[float] = None
            try:
                import soundfile as sf  # type: ignore[import-not-found]

                info = sf.info(str(audio_path))
                total_seconds = float(info.frames) / float(info.samplerate) if info.samplerate else None
            except Exception:
                pass
            if total_seconds is None:
                # Fall back to ffprobe.
                try:
                    import json as _json
                    import subprocess as _sp

                    proc = _sp.run(
                        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(audio_path)],
                        stdout=_sp.PIPE, stderr=_sp.PIPE, check=False,
                    )
                    if proc.returncode == 0:
                        payload = _json.loads(proc.stdout.decode("utf-8", errors="replace"))
                        total_seconds = float((payload.get("format") or {}).get("duration") or 0) or None
                except Exception:
                    pass

            # Phase: TRANSCRIBING
            _update_job(
                job_path,
                status="RUNNING",
                progress={"percent": 0.0, "processed_seconds": 0.0, "total_seconds": total_seconds, "phase": "TRANSCRIBING"},
            )

            segments_list: list[dict] = []
            language_probability: Optional[float] = None
            detected_language: Optional[str] = None
            last_progress_write = 0.0
            used_vad = True

            def _run_transcribe(vad: bool) -> None:
                """Run the transcription and populate segments_list."""
                nonlocal language_probability, detected_language, last_progress_write
                segments_list.clear()
                segments_iter, info = model.transcribe(
                    str(audio_path),
                    language=language,
                    task="transcribe",
                    word_timestamps=True,
                    vad_filter=vad,
                    beam_size=5,
                    condition_on_previous_text=True,
                )
                language_probability = getattr(info, "language_probability", None)
                detected_language = getattr(info, "language", None)

                for seg in segments_iter:
                    words: list[dict] = []
                    for w in (getattr(seg, "words", None) or []):
                        words.append({
                            "start": float(getattr(w, "start", 0.0)),
                            "end": float(getattr(w, "end", 0.0)),
                            "text": str(getattr(w, "word", "")),
                            "probability": float(getattr(w, "probability", 0.0)) or None,
                        })
                    segments_list.append({
                        "id": int(getattr(seg, "id", len(segments_list))),
                        "start": float(getattr(seg, "start", 0.0)),
                        "end": float(getattr(seg, "end", 0.0)),
                        "text": str(getattr(seg, "text", "")).strip(),
                        "avg_logprob": float(getattr(seg, "avg_logprob", 0.0)) or None,
                        "no_speech_probability": float(getattr(seg, "no_speech_probability", 0.0)) or None,
                        "words": words,
                    })
                    # Update progress at most every 0.5s.
                    now = time.monotonic()
                    if now - last_progress_write >= 0.5:
                        last_progress_write = now
                        processed = float(getattr(seg, "end", 0.0))
                        percent = None
                        if total_seconds and total_seconds > 0:
                            percent = min(100.0, max(0.0, processed / total_seconds * 100.0))
                        _update_job(
                            job_path,
                            progress={
                                "percent": percent,
                                "processed_seconds": processed,
                                "total_seconds": total_seconds,
                                "phase": "TRANSCRIBING",
                            },
                        )

            try:
                _run_transcribe(vad=True)
            except Exception as exc:
                _update_job(
                    job_path,
                    status="FAILED",
                    error=f"transcription failed: {type(exc).__name__}: {exc}",
                )
                return 1

            # If VAD filtered everything out, retry without VAD.
            if not segments_list and used_vad:
                logger.warning("VAD filter produced no segments for %s, retrying without VAD", audio_path.name)
                _update_job(
                    job_path,
                    progress={"percent": 0.0, "processed_seconds": 0.0, "total_seconds": total_seconds, "phase": "TRANSCRIBING_NO_VAD"},
                )
                used_vad = False
                try:
                    _run_transcribe(vad=False)
                except Exception as exc:
                    _update_job(
                        job_path,
                        status="FAILED",
                        error=f"transcription (no VAD) failed: {type(exc).__name__}: {exc}",
                    )
                    return 1

            if not segments_list:
                audio_size = audio_path.stat().st_size if audio_path.is_file() else 0
                _update_job(
                    job_path,
                    status="FAILED",
                    error=(
                        f"transcription produced no segments. "
                        f"audio={audio_path.name} size={audio_size}B "
                        f"duration={total_seconds}s lang={language or 'auto'} "
                        f"detected={detected_language} model={model_name}. "
                        f"The audio may be silent or corrupt."
                    ),
                )
                return 1

            # Phase: EXPORTING
            _update_job(
                job_path,
                status="EXPORTING",
                progress={"percent": 100.0, "processed_seconds": total_seconds, "total_seconds": total_seconds, "phase": "EXPORTING"},
            )

            transcript_dir.mkdir(parents=True, exist_ok=True)
            duration = total_seconds or float(segments_list[-1].get("end", 0.0))

            transcript_payload = {
                "schema_version": 1,
                "id": transcription_id,
                "source_type": source_type,
                "source_id": source_id,
                "audio_artifact": "artifacts/audio/source_audio.flac",
                "model": model_name,
                "device": fw_device,
                "compute_type": compute_type,
                "language": detected_language or language,
                "language_probability": language_probability,
                "duration_seconds": duration,
                "created_at": _now_iso(),
                "segments": segments_list,
            }

            # Write all formats atomically.
            json_path = transcript_dir / TRANSCRIPT_JSON
            txt_path = transcript_dir / TRANSCRIPT_TXT
            srt_path = transcript_dir / TRANSCRIPT_SRT
            vtt_path = transcript_dir / TRANSCRIPT_VTT
            meta_path = transcript_dir / TRANSCRIPT_METADATA

            _atomic_write_json(json_path, transcript_payload)
            _atomic_write_text(txt_path, _export_txt(segments_list))
            _atomic_write_text(srt_path, _export_srt(segments_list))
            _atomic_write_text(vtt_path, _export_vtt(segments_list))

            meta = {
                "schema_version": 1,
                "id": transcription_id,
                "source_type": source_type,
                "source_id": source_id,
                "audio_artifact": "artifacts/audio/source_audio.flac",
                "model": model_name,
                "device": fw_device,
                "compute_type": compute_type,
                "language": transcript_payload["language"],
                "language_probability": language_probability,
                "duration_seconds": duration,
                "created_at": transcript_payload["created_at"],
                "status": "READY",
                "segment_count": len(segments_list),
                "files": {
                    "json": TRANSCRIPT_JSON,
                    "txt": TRANSCRIPT_TXT,
                    "srt": TRANSCRIPT_SRT,
                    "vtt": TRANSCRIPT_VTT,
                },
                "produced_by_job_id": job_id,
            }
            _atomic_write_json(meta_path, meta)

            result = {
                "transcription_id": transcription_id,
                "audio_artifact": transcript_payload["audio_artifact"],
                "model": model_name,
                "device": fw_device,
                "compute_type": compute_type,
                "language": transcript_payload["language"],
                "language_probability": language_probability,
                "duration_seconds": duration,
                "segment_count": len(segments_list),
            }
            _update_job(
                job_path,
                status="READY",
                result=result,
                completed_at=_now_iso(),
                progress={"percent": 100.0, "processed_seconds": total_seconds, "total_seconds": total_seconds, "phase": None},
            )
            return 0
    except GpuLockBusyError as exc:
        # The GPU is busy. Stay WAITING_FOR_GPU so the parent can retry
        # later. But since this worker process is exiting, the parent's
        # reaper will mark us FAILED. We instead write a clear error and
        # let the user retry.
        owner = exc.owner
        owner_type = owner.get("owner_type", "unknown")
        _update_job(
            job_path,
            status="WAITING_FOR_GPU",
            error=f"GPU is busy ({owner_type}). Retry later.",
            progress={"percent": None, "processed_seconds": None, "total_seconds": None, "phase": "WAITING_FOR_GPU"},
        )
        # Return 0 so the parent reaper does not mark us FAILED. The job
        # stays WAITING_FOR_GPU and the user can retry.
        return 0
    except Exception as exc:
        _update_job(
            job_path,
            status="FAILED",
            error=f"transcription worker crashed: {type(exc).__name__}: {exc}",
        )
        return 1


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m media_processing.transcription_worker <worker_job.json>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run_worker(sys.argv[1])
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("transcription worker crashed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
