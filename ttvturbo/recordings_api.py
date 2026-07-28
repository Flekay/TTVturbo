"""Recordings API router — ``/api/recordings/*``.

Extracted from ``app_factory.py`` so the factory stays a thin wiring
layer.  The handlers read from a :class:`ServiceContainer`-like object
at request time.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ttvturbo.settings import (
    ALLOWED_UPLOAD_EXTENSIONS,
    ALLOWED_UPLOAD_MIME_TYPES,
    MAX_UPLOAD_BYTES,
)
from ttvturbo.system.executables import find_executable

logger = logging.getLogger("ttvturbo")


def _read_wav_duration(path: Path) -> float | None:
    import wave

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
    if not name.lower().endswith(".wav"):
        return True
    if name.startswith(".") or name.startswith("~"):
        return True
    lower = name.lower()
    for suffix in (".tmp", ".part", ".bak", ".swp"):
        if lower.endswith(suffix):
            return True
    return False


def _safe_filename(filename: str) -> str | None:
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


def build_recordings_router(container: Any) -> APIRouter:
    """Build the recordings router.

    *container* must expose ``paths`` (with ``recordings``) and
    ``voice_profile_service``.
    """
    router = APIRouter(tags=["recordings"])

    def _recordings_dir() -> Path:
        assert container.paths is not None
        return container.paths.recordings

    def _list_recordings() -> list[dict]:
        import datetime as _dt

        rec_dir = _recordings_dir()
        items: list[dict] = []
        for entry in rec_dir.iterdir():
            if not entry.is_file():
                continue
            if _is_temp_or_hidden(entry.name):
                continue
            duration = _read_wav_duration(entry)
            if duration is None:
                continue
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

    @router.post("/api/recordings")
    async def upload_recording(audio: UploadFile = File(...)) -> JSONResponse:
        ffmpeg_path = find_executable("ffmpeg")
        if ffmpeg_path is None:
            raise HTTPException(
                status_code=500,
                detail="ffmpeg not found on server PATH. Install FFmpeg to convert recordings.",
            )

        if not audio.filename:
            raise HTTPException(status_code=400, detail="No filename provided.")

        suffix = Path(audio.filename).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file extension: {suffix or '(none)'}.",
            )
        content_type = (audio.content_type or "").lower().split(";", 1)[0].strip()
        if content_type and content_type not in ALLOWED_UPLOAD_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported content type: {content_type}.",
            )

        recording_id = uuid.uuid4().hex
        suffix = suffix or ".webm"
        tmp_in = Path(tempfile.gettempdir()) / f"ttvturbo_{recording_id}{suffix}"
        wav_name = f"{recording_id}.wav"
        wav_path = _recordings_dir() / wav_name

        try:
            total = 0
            with tmp_in.open("wb") as fh:
                while True:
                    chunk = await audio.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        fh.close()
                        tmp_in.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=f"Upload exceeds {MAX_UPLOAD_BYTES} bytes.",
                        )
                    fh.write(chunk)
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - disk error path
            tmp_in.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Failed to store upload: {exc}") from exc

        if tmp_in.stat().st_size == 0:
            tmp_in.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

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

        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            tmp_in.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="WAV file missing or empty after conversion.")

        ffprobe = find_executable("ffprobe") or ffmpeg_path
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

    @router.get("/api/recordings")
    def list_recordings() -> JSONResponse:
        return JSONResponse(content={"recordings": _list_recordings()})

    @router.get("/api/recordings/{filename}")
    def get_recording(filename: str) -> FileResponse:
        safe = Path(filename).name
        if safe != filename:
            raise HTTPException(status_code=400, detail="Invalid filename.")
        if not safe.lower().endswith(".wav"):
            raise HTTPException(status_code=400, detail="Invalid filename.")
        wav_path = _recordings_dir() / safe
        if not wav_path.is_file():
            raise HTTPException(status_code=404, detail="Recording not found.")
        return FileResponse(wav_path, media_type="audio/wav", filename=safe)

    @router.delete("/api/recordings/{filename}")
    def delete_recording(filename: str) -> JSONResponse:
        safe = _safe_filename(filename)
        if safe is None:
            raise HTTPException(status_code=400, detail="Invalid filename.")
        wav_path = _recordings_dir() / safe
        if not wav_path.is_file():
            raise HTTPException(status_code=404, detail="Recording not found.")
        try:
            using_profiles = container.voice_profile_service.find_profiles_using_recording(safe)
        except Exception:  # pragma: no cover - defensive
            using_profiles = []
        if using_profiles:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "recording_in_use",
                        "message": "Die Aufnahme wird von einem Voice Profile verwendet.",
                        "profiles": [
                            {"id": p.get("id"), "name": p.get("name")}
                            for p in using_profiles
                        ],
                    }
                },
            )
        try:
            wav_path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not delete file: {exc}") from exc
        return JSONResponse(content={"filename": safe, "deleted": True})

    return router
