"""Audio-extraction worker subprocess entry point.

Launched by :class:`media_processing.audio_extraction.AudioExtractionService`
as ``python -m media_processing.audio_extraction_worker <worker_job.json>``.

The worker:

1. reads the worker job file (paths only — no client-supplied FFmpeg args);
2. resolves the source file path (already verified by the service);
3. runs FFmpeg to produce ``source_audio.flac.part`` (mono, 16 kHz, FLAC,
   no normalization, no denoising, no speed change);
4. captures FFmpeg progress from stderr (parsed, not piped to the parent);
5. verifies the result with ffprobe (duration, sample rate, channels, codec);
6. computes SHA-256 and file size;
7. atomically renames ``.part`` -> ``source_audio.flac``;
8. writes the sidecar ``metadata.json`` atomically;
9. updates the job record to READY.

No ``shell=True``, no shell strings. The FFmpeg argument list is fixed
in this module; the client cannot inject arbitrary arguments.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ttvturbo.media_processing.audio_worker")

PART_SUFFIX = ".part"
FLAC_FILENAME = "source_audio.flac"
METADATA_FILENAME = "metadata.json"


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


def _update_job(job_path: Path, **updates: Any) -> None:
    """Atomically update the job record with the given fields."""
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


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        try:
            from ttvturbo.app import _find_executable  # type: ignore[import-not-found]

            found = _find_executable("ffmpeg")
        except Exception:
            pass
    if not found:
        raise RuntimeError("ffmpeg not found on PATH")
    return found


def _find_ffprobe() -> str:
    found = shutil.which("ffprobe")
    if not found:
        try:
            from ttvturbo.app import _find_executable  # type: ignore[import-not-found]

            found = _find_executable("ffprobe")
        except Exception:
            pass
    if not found:
        raise RuntimeError("ffprobe not found on PATH")
    return found


def _ffprobe_audio_info(path: Path) -> dict:
    ffprobe = _find_ffprobe()
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise RuntimeError(f"ffprobe failed: {stderr}")
    payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    streams = payload.get("streams") or []
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio_stream is None:
        raise RuntimeError("no audio stream found in FLAC")
    fmt = payload.get("format") or {}
    duration = None
    try:
        duration = float(fmt.get("duration") or audio_stream.get("duration") or 0)
    except (TypeError, ValueError):
        duration = None
    return {
        "codec": audio_stream.get("codec_name") or "flac",
        "sample_rate": int(audio_stream.get("sample_rate") or 16000),
        "channels": int(audio_stream.get("channels") or 1),
        "duration_seconds": duration,
    }


def _parse_ffmpeg_progress(line: str, total_seconds: Optional[float]) -> Optional[dict]:
    """Parse an FFmpeg stderr progress line.

    FFmpeg writes lines like ``out_time_ms=12345000`` when ``-progress``
    is used, or ``time=00:01:23.45`` in the default summary. We handle
    both.
    """
    m = re.search(r"out_time_ms=(\d+)", line)
    if m:
        try:
            ms = int(m.group(1))
            processed = ms / 1_000_000.0
        except ValueError:
            return None
    else:
        m = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
        if not m:
            return None
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        processed = h * 3600 + mi * 60 + s
    percent = None
    if total_seconds and total_seconds > 0:
        percent = min(100.0, max(0.0, processed / total_seconds * 100.0))
    return {
        "processed_seconds": processed,
        "total_seconds": total_seconds,
        "percent": percent,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ffprobe_source_duration(path: Path) -> Optional[float]:
    try:
        ffprobe = _find_ffprobe()
        cmd = [
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", str(path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            return None
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        fmt = payload.get("format") or {}
        try:
            return float(fmt.get("duration") or 0)
        except (TypeError, ValueError):
            return None
    except Exception:
        return None


def run_worker(worker_job_path: str) -> int:
    try:
        with open(worker_job_path, "r", encoding="utf-8") as fh:
            wjob = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("could not read worker job %s: %s", worker_job_path, exc)
        return 2
    job_path = Path(wjob["job_path"])
    source_file = Path(wjob["source_file"])
    artifact_dir = Path(wjob["artifact_dir"])
    audio_filename = wjob.get("audio_filename", FLAC_FILENAME)
    metadata_filename = wjob.get("audio_metadata_filename", METADATA_FILENAME)
    source_type = wjob.get("source_type", "twitch_vod")
    source_id = wjob["source_id"]

    if not source_file.is_file():
        _update_job(job_path, status="FAILED", error=f"source file missing: {source_file.name}")
        return 1

    artifact_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifact_dir / audio_filename
    part_path = artifact_dir / (audio_filename + PART_SUFFIX)
    # Remove any stale .part from a previous attempt.
    try:
        if part_path.exists():
            part_path.unlink()
    except OSError:
        pass

    total_seconds = _ffprobe_source_duration(source_file)

    # Mark RUNNING.
    _update_job(
        job_path,
        status="RUNNING",
        started_at=_now_iso(),
        progress={"percent": 0.0 if total_seconds else None, "processed_seconds": 0.0, "total_seconds": total_seconds, "phase": None},
    )

    ffmpeg = _find_ffmpeg()
    # Fixed FFmpeg argument list. No client-supplied args. Mono, 16 kHz,
    # FLAC, lossless, no normalization, no denoising, no speed change.
    # The output format is set explicitly with -f flac because the .part
    # suffix prevents FFmpeg from inferring it from the filename.
    cmd = [
        ffmpeg, "-hide_banner", "-y",
        "-i", str(source_file),
        "-vn",  # no video
        "-ac", "1",  # mono
        "-ar", "16000",  # 16 kHz
        "-c:a", "flac",
        "-compression_level", "5",
        "-f", "flac",
        # No loudness normalization, no denoise, no atempo.
        str(part_path),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        _update_job(job_path, status="FAILED", error=f"could not start ffmpeg: {exc}")
        return 1

    # Read stderr line by line to capture progress. We must drain it
    # continuously to avoid deadlocking the pipe.
    last_progress_write = 0.0
    stderr_lines: list[str] = []
    assert proc.stderr is not None
    for raw in proc.stderr:
        try:
            line = raw.decode("utf-8", errors="replace").rstrip()
        except Exception:
            line = ""
        if line:
            stderr_lines.append(line)
        prog = _parse_ffmpeg_progress(line, total_seconds)
        if prog is not None:
            now = time.monotonic()
            # Throttle progress writes to at most every 0.5s.
            if now - last_progress_write >= 0.5:
                last_progress_write = now
                _update_job(
                    job_path,
                    progress={
                        "percent": prog["percent"],
                        "processed_seconds": prog["processed_seconds"],
                        "total_seconds": prog["total_seconds"],
                        "phase": None,
                    },
                )
    exit_code = proc.wait()
    if exit_code != 0:
        tail = "\n".join(stderr_lines[-20:])[-1000:]
        try:
            if part_path.exists():
                part_path.unlink()
        except OSError:
            pass
        _update_job(
            job_path,
            status="FAILED",
            error=f"ffmpeg exited with code {exit_code}. {tail}",
        )
        return 1

    if not part_path.is_file():
        _update_job(job_path, status="FAILED", error="ffmpeg produced no output file")
        return 1

    # Verify with ffprobe.
    _update_job(job_path, status="EXPORTING", progress={"percent": 100.0, "processed_seconds": total_seconds, "total_seconds": total_seconds, "phase": "EXPORTING"})
    try:
        info = _ffprobe_audio_info(part_path)
    except Exception as exc:
        try:
            part_path.unlink()
        except OSError:
            pass
        _update_job(job_path, status="FAILED", error=f"ffprobe verification failed: {exc}")
        return 1

    if info["sample_rate"] != 16000:
        try:
            part_path.unlink()
        except OSError:
            pass
        _update_job(
            job_path,
            status="FAILED",
            error=f"sample rate mismatch: expected 16000, got {info['sample_rate']}",
        )
        return 1
    if info["channels"] != 1:
        try:
            part_path.unlink()
        except OSError:
            pass
        _update_job(
            job_path,
            status="FAILED",
            error=f"channel count mismatch: expected 1 (mono), got {info['channels']}",
        )
        return 1

    duration = info["duration_seconds"] or total_seconds or 0.0
    file_size = part_path.stat().st_size
    sha = _sha256(part_path)

    # Atomically rename .part -> final.
    try:
        os.replace(part_path, out_path)
    except OSError as exc:
        try:
            part_path.unlink()
        except OSError:
            pass
        _update_job(job_path, status="FAILED", error=f"could not finalize audio file: {exc}")
        return 1

    # Write sidecar metadata.json.
    meta = {
        "schema_version": 1,
        "source_type": source_type,
        "source_id": source_id,
        "file_name": audio_filename,
        "container": "flac",
        "sample_rate": info["sample_rate"],
        "channels": info["channels"],
        "codec": info["codec"],
        "duration_seconds": duration,
        "file_size_bytes": file_size,
        "sha256": sha,
        "created_at": _now_iso(),
        "produced_by_job_id": wjob.get("job_id"),
    }
    meta_path = artifact_dir / metadata_filename
    _atomic_write_json(meta_path, meta)

    result = {
        "audio_artifact": f"artifacts/audio/{audio_filename}",
        "file_name": audio_filename,
        "duration_seconds": duration,
        "file_size_bytes": file_size,
        "sha256": sha,
        "sample_rate": info["sample_rate"],
        "channels": info["channels"],
        "codec": info["codec"],
    }
    _update_job(
        job_path,
        status="READY",
        result=result,
        completed_at=_now_iso(),
        progress={"percent": 100.0, "processed_seconds": total_seconds, "total_seconds": total_seconds, "phase": None},
    )
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m media_processing.audio_extraction_worker <worker_job.json>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run_worker(sys.argv[1])
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("audio extraction worker crashed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
