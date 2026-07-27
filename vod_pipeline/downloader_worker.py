"""VOD download worker subprocess.

Runs in a separate Python process (spawned by :class:`VodPipelineService`)
so a multi-hour yt-dlp download never blocks the FastAPI request loop.

The worker:

* reads a job.json (vod_id, source_url, output_dir, metadata_path);
* uses the yt-dlp Python API with a real ``progress_hook``;
* writes real progress (percent, bytes, speed, eta) to metadata.json,
  throttled to at most a few times per second / on relevant change;
* on success, sets status ``VERIFYING`` and lets the parent run FFprobe;
* on error, sets status ``FAILED`` with a concrete reason;
* never accepts arbitrary output templates from user input - the final
  file is always ``source.<container>`` inside the VOD directory.

The parent process is responsible for FFprobe verification and for
marking the record ``READY``. This keeps the heavy yt-dlp dependency
out of the FastAPI process and lets the worker fail without taking the
server down.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .schemas import (
    ALLOWED_SOURCE_CONTAINERS,
    VodStatus,
    empty_download,
    empty_progress,
)

logger = logging.getLogger("ttvturbo.vod_pipeline.worker")

# Guards metadata writes: yt-dlp calls the progress hook from multiple
# threads (concurrent_fragment_downloads > 1), so without a lock two
# threads race on the same metadata.json.tmp file and os.replace fails
# with FileNotFoundError on the thread that loses the race.
_metadata_lock = threading.Lock()


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Atomically write JSON to ``path``.

    Thread-safe: uses a per-call unique tmp filename so concurrent calls
    never collide on the same temp file, plus a lock to serialize the
    os.replace step.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique tmp name per call — no collision between concurrent threads.
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
    )
    last_exc: Optional[Exception] = None
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            with _metadata_lock:
                os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
        except OSError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass
    raise last_exc  # type: ignore[misc]


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    # Retry on PermissionError: on Windows, os.replace() in another thread
    # briefly holds an exclusive lock on the target file.
    for attempt in range(5):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
        except (OSError, json.JSONDecodeError):
            return None
    return None


class _ProgressThrottle:
    """Persist progress at most every ``interval`` seconds or on status change.

    Thread-safe: yt-dlp calls the progress hook from multiple threads when
    ``concurrent_fragment_downloads`` > 1.
    """

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = float(interval)
        self._last_write = 0.0
        self._last_percent: Optional[float] = None
        self._lock = threading.Lock()

    def should_write(self, percent: Optional[float]) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._last_write == 0.0:
                self._last_write = now
                return True
            # Always allow a write if percent changed by >= 1.
            if percent is not None and (
                self._last_percent is None or abs(percent - self._last_percent) >= 1.0
            ):
                self._last_percent = percent
                self._last_write = now
                return True
            self._last_percent = percent
            if (now - self._last_write) >= self.interval:
                self._last_write = now
                return True
            return False


def _build_progress_hook(
    metadata_path: Path, throttle: _ProgressThrottle
) -> "Any":
    # The entire read-modify-write cycle must be serialized: yt-dlp calls
    # this hook from multiple threads (concurrent_fragment_downloads > 1),
    # and without a lock two threads race on the read + write, causing
    # PermissionError on Windows when one thread's os.replace coincides
    # with another thread's open() for reading.
    hook_lock = threading.Lock()

    def _hook(d: dict) -> None:
        status = d.get("status")
        with hook_lock:
            meta = _read_json(metadata_path) or {}
            progress = dict(meta.get("progress") or empty_progress())
            download = dict(meta.get("download") or empty_download())
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes")
                speed = d.get("speed")
                eta = d.get("eta")
                percent = None
                if total and downloaded is not None and total > 0:
                    percent = round((downloaded / total) * 100.0, 2)
                progress.update({
                    "percent": percent,
                    "downloaded_bytes": int(downloaded) if downloaded is not None else None,
                    "total_bytes": int(total) if total is not None else None,
                    "speed_bytes_per_second": float(speed) if speed is not None else None,
                    "eta_seconds": float(eta) if eta is not None else None,
                })
                meta["status"] = VodStatus.DOWNLOADING.value
                meta["progress"] = progress
                meta["updated_at"] = _now_iso()
                if throttle.should_write(percent):
                    _atomic_write_json(metadata_path, meta)
            elif status == "finished":
                # yt-dlp finished downloading the file; postprocessing may follow.
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes") or total
                progress.update({
                    "downloaded_bytes": int(downloaded) if downloaded is not None else None,
                    "total_bytes": int(total) if total is not None else None,
                    "percent": 100.0 if total else None,
                    "speed_bytes_per_second": None,
                    "eta_seconds": None,
                })
                meta["status"] = VodStatus.DOWNLOADING.value
                meta["progress"] = progress
                meta["updated_at"] = _now_iso()
                _atomic_write_json(metadata_path, meta)
            elif status == "error":
                meta["status"] = VodStatus.FAILED.value
                meta["error"] = "yt-dlp reported a download error."
                meta["progress"] = empty_progress()
                meta["updated_at"] = _now_iso()
                _atomic_write_json(metadata_path, meta)

    return _hook


def _pick_container(info: dict) -> str:
    ext = (info.get("ext") or "").lower()
    if ext in ALLOWED_SOURCE_CONTAINERS:
        return ext
    return "mp4"


def _resolve_final_path(output_dir: Path, container: str) -> Path:
    """Always a fixed, safe filename inside the VOD directory."""
    safe_container = container if container in ALLOWED_SOURCE_CONTAINERS else "mp4"
    return output_dir / f"source.{safe_container}"


def run_worker(job_path: str) -> int:
    """Entry point for the subprocess: ``python -m vod_pipeline.downloader_worker <job>``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    try:
        with open(job_path, "r", encoding="utf-8") as fh:
            job = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read job file %s: %s", job_path, exc)
        return 2

    vod_id = job.get("vod_id")
    source_url = job.get("source_url")
    output_dir_str = job.get("output_dir")
    metadata_path_str = job.get("metadata_path")
    if not (vod_id and source_url and output_dir_str and metadata_path_str):
        logger.error("job file is missing required fields")
        return 2

    output_dir = Path(output_dir_str)
    metadata_path = Path(metadata_path_str)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import yt_dlp
    except ImportError as exc:
        _mark_failed(metadata_path, f"yt-dlp is not installed: {exc}")
        return 3

    meta = _read_json(metadata_path) or {}
    meta["status"] = VodStatus.DOWNLOADING.value
    meta["download"]["started_at"] = _now_iso()
    meta["error"] = None
    meta["progress"] = empty_progress()
    meta["updated_at"] = _now_iso()
    _atomic_write_json(metadata_path, meta)

    throttle = _ProgressThrottle(interval=0.5)
    hook = _build_progress_hook(metadata_path, throttle)

    # The final file is always source.<container> inside the VOD dir.
    # We let yt-dlp download to a .part temp via outtmpl, then rename the
    # final file to source.<ext>. We never derive a filename from the
    # Twitch title.
    final_container = "mp4"
    final_path = _resolve_final_path(output_dir, final_container)
    # Use a unique temp basename so concurrent runs never collide.
    tmp_basename = f".dl_{vod_id}"
    outtmpl = str(output_dir / f"{tmp_basename}.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "progress_hooks": [hook],
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "noprogress": True,
        "quiet": True,
        "no_warnings": False,
        "retries": 0,
        "fragment_retries": 0,
        "noplaylist": True,
        "concurrent_fragment_downloads": 4,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(source_url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        _mark_failed(metadata_path, f"yt-dlp download failed: {exc}")
        _cleanup_partials(output_dir, tmp_basename)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        _mark_failed(metadata_path, f"yt-dlp download failed: {exc}")
        _cleanup_partials(output_dir, tmp_basename)
        return 1

    # Find the downloaded file. yt-dlp writes to outtmpl; the final ext
    # may differ from the requested merge format if muxing produced mkv.
    final_container = _pick_container(info or {})
    final_path = _resolve_final_path(output_dir, final_container)

    downloaded = _find_downloaded_file(output_dir, tmp_basename)
    if downloaded is None:
        _mark_failed(metadata_path, "yt-dlp finished but no output file was found.")
        return 1

    # Rename to the canonical source.<container>. If a stale final file
    # exists (e.g. from a previous attempt), overwrite it.
    try:
        if final_path.exists():
            final_path.unlink()
        os.replace(downloaded, final_path)
    except OSError as exc:
        _mark_failed(metadata_path, f"could not finalize download file: {exc}")
        return 1

    _cleanup_partials(output_dir, tmp_basename)

    # Hand off to the parent for FFprobe verification. The parent will
    # set VERIFYING -> READY/FAILED. We only record the final filename /
    # size so the parent can verify them.
    meta = _read_json(metadata_path) or {}
    download = dict(meta.get("download") or empty_download())
    try:
        download["file_name"] = final_path.name
        download["file_size_bytes"] = final_path.stat().st_size
        download["container"] = final_container
    except OSError:
        pass
    meta["download"] = download
    meta["status"] = VodStatus.VERIFYING.value
    meta["progress"] = empty_progress()
    meta["updated_at"] = _now_iso()
    _atomic_write_json(metadata_path, meta)
    return 0


def _mark_failed(metadata_path: Path, reason: str) -> None:
    meta = _read_json(metadata_path) or {}
    meta["status"] = VodStatus.FAILED.value
    meta["error"] = reason
    meta["progress"] = empty_progress()
    if not meta.get("download", {}).get("completed_at"):
        meta.setdefault("download", {})["completed_at"] = None
    meta["updated_at"] = _now_iso()
    try:
        _atomic_write_json(metadata_path, meta)
    except OSError:  # pragma: no cover - defensive
        pass


def _find_downloaded_file(output_dir: Path, tmp_basename: str) -> Optional[Path]:
    """Return the real downloaded file produced by yt-dlp.

    yt-dlp may produce ``.dl_<id>.mp4``, ``.dl_<id>.mkv``, or a
    ``.dl_<id>.mp4.part`` if it failed. We pick the largest non-.part
    real file matching the tmp basename.
    """
    candidates: list[Path] = []
    for entry in output_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if not name.startswith(tmp_basename):
            continue
        if name.endswith(".part") or name.endswith(".tmp"):
            continue
        candidates.append(entry)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def _cleanup_partials(output_dir: Path, tmp_basename: str) -> None:
    for entry in output_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith(tmp_basename):
            try:
                entry.unlink()
            except OSError:
                pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m vod_pipeline.downloader_worker <job.json>", file=sys.stderr)
        return 2
    return run_worker(argv[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
