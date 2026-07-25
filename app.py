"""Minimal local browser app for real microphone recordings.

Endpoints:
  GET  /                          -> serves static/index.html
  POST /api/recordings            -> receives a browser recording, converts to WAV via FFmpeg
  GET  /api/recordings            -> lists all stored WAV recordings (newest first)
  GET  /api/recordings/{filename} -> streams a stored WAV file
  DELETE /api/recordings/{filename} -> deletes a stored WAV file
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import wave
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("ttvturbo")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RECORDINGS_DIR = BASE_DIR / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="TTVturbo")

# Serve the static frontend (index.html, app.js, style.css) at /.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _find_executable(name: str) -> str | None:
    """Find an executable by name.

    Tries PATH first, then falls back to common Windows install locations
    (winget, Program Files, C:\\ffmpeg) so the app still works in shells
    whose PATH was not refreshed after installing FFmpeg.
    """
    found = shutil.which(name)
    if found:
        return found

    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        candidates = []
        if local_app:
            candidates.append(
                Path(local_app)
                / "Microsoft"
                / "WinGet"
                / "Packages"
            )
        candidates += [
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
            Path("C:/ffmpeg"),
            Path("C:/tools/ffmpeg"),
        ]
        for base in candidates:
            if not base.exists():
                continue
            for hit in base.rglob(f"{name}.exe"):
                return str(hit)
    return None


def _ffmpeg_available() -> str | None:
    """Return the ffmpeg executable path or None if not found."""
    return _find_executable("ffmpeg")


def _read_wav_duration(path: Path) -> float | None:
    """Return the WAV duration in seconds, read from the real file.

    Returns None if the file is not a valid, readable WAV.
    """
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
    """Filter out temp/partial/hidden files that should not be listed."""
    if not name.lower().endswith(".wav"):
        return True
    if name.startswith(".") or name.startswith("~"):
        return True
    lower = name.lower()
    for suffix in (".tmp", ".part", ".bak", ".swp"):
        if lower.endswith(suffix):
            return True
    return False


def _list_recordings() -> list[dict]:
    """Scan RECORDINGS_DIR and return metadata for all valid WAVs, newest first."""
    items: list[dict] = []
    for entry in RECORDINGS_DIR.iterdir():
        if not entry.is_file():
            continue
        if _is_temp_or_hidden(entry.name):
            continue
        duration = _read_wav_duration(entry)
        if duration is None:
            continue  # corrupted or unreadable WAV -> logged, skipped
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


def _safe_filename(filename: str) -> str | None:
    """Return a plain filename if safe, else None.

    Blocks path traversal, absolute paths, hidden/temp files.
    """
    if not filename:
        return None
    if "/" in filename or "\\" in filename:
        return None
    if filename.startswith(".") or filename.startswith("~"):
        return None
    safe = Path(filename).name
    if safe != filename:
        return None
    if not safe.lower().endswith(".wav"):
        return None
    return safe


@app.get("/")
def index() -> FileResponse:
    index_html = STATIC_DIR / "index.html"
    if not index_html.is_file():
        raise HTTPException(status_code=404, detail="static/index.html not found")
    return FileResponse(index_html, media_type="text/html")


@app.post("/api/recordings")
async def upload_recording(audio: UploadFile = File(...)) -> JSONResponse:
    ffmpeg_path = _ffmpeg_available()
    if ffmpeg_path is None:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg not found on server PATH. Install FFmpeg to convert recordings.",
        )

    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    # Preserve the original extension so FFmpeg can demux correctly.
    suffix = Path(audio.filename).suffix.lower() or ".webm"
    recording_id = uuid.uuid4().hex
    tmp_in = Path(tempfile.gettempdir()) / f"ttvturbo_{recording_id}{suffix}"
    wav_name = f"{recording_id}.wav"
    wav_path = RECORDINGS_DIR / wav_name

    try:
        with tmp_in.open("wb") as fh:
            while True:
                chunk = await audio.read(1 << 20)  # 1 MiB
                if not chunk:
                    break
                fh.write(chunk)
    except Exception as exc:  # pragma: no cover - disk error path
        tmp_in.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to store upload: {exc}") from exc

    if tmp_in.stat().st_size == 0:
        tmp_in.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Convert to a real, playable WAV (PCM 16-bit little-endian).
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", str(tmp_in),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "1",
        str(wav_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        tmp_in.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"ffmpeg not executable: {exc}") from exc

    if proc.returncode != 0:
        tmp_in.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        stderr = proc.stderr.decode("utf-8", errors="replace")[-2000:]
        raise HTTPException(status_code=500, detail=f"ffmpeg conversion failed: {stderr}")

    # Verify the WAV exists, is non-empty, and is readable via ffprobe.
    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        tmp_in.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="WAV file missing or empty after conversion.")

    ffprobe = _find_executable("ffprobe") or ffmpeg_path
    probe = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,channels,sample_rate",
            "-of", "default=noprint_wrappers=1",
            str(wav_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if probe.returncode != 0:
        tmp_in.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        stderr = probe.stderr.decode("utf-8", errors="replace")[-2000:]
        raise HTTPException(status_code=500, detail=f"WAV not playable: {stderr}")

    tmp_in.unlink(missing_ok=True)

    return JSONResponse(
        status_code=201,
        content={
            "filename": wav_name,
            "url": f"/api/recordings/{wav_name}",
            "size_bytes": wav_path.stat().st_size,
            "probe": probe.stdout.decode("utf-8", errors="replace").strip(),
        },
    )


@app.get("/api/recordings")
def list_recordings() -> JSONResponse:
    return JSONResponse(content={"recordings": _list_recordings()})


@app.get("/api/recordings/{filename}")
def get_recording(filename: str) -> FileResponse:
    # Reject path traversal attempts.
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    wav_path = RECORDINGS_DIR / safe
    if not wav_path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    return FileResponse(wav_path, media_type="audio/wav", filename=safe)


@app.delete("/api/recordings/{filename}")
def delete_recording(filename: str) -> JSONResponse:
    safe = _safe_filename(filename)
    if safe is None:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    wav_path = RECORDINGS_DIR / safe
    if not wav_path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    try:
        wav_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not delete file: {exc}") from exc
    return JSONResponse(content={"filename": safe, "deleted": True})


def main() -> None:
    import uvicorn

    if _ffmpeg_available() is None:
        print("WARNING: ffmpeg not found on PATH. WAV conversion will fail.", file=sys.stderr)

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
