"""Media capability helper functions with no domain-specific policy."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ttvturbo.library.storage import sanitize_container


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe_json(ffprobe_path: str, path: Path) -> dict[str, Any]:
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-1000:]}")
    payload = json.loads(proc.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe returned invalid JSON")
    return payload


def video_metadata(ffprobe_path: str, path: Path) -> dict[str, Any]:
    payload = ffprobe_json(ffprobe_path, path)
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("media has no video stream")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("video dimensions are invalid")
    duration = video.get("duration") or (payload.get("format") or {}).get("duration") or 0
    try:
        duration_f = float(duration)
    except (TypeError, ValueError):
        duration_f = 0.0
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        n, d = str(rate).split("/", 1)
        fps = float(n) / float(d) if float(d) else 0.0
    except Exception:
        fps = 0.0
    return {
        "width": width,
        "height": height,
        "duration_seconds": max(0.0, duration_f),
        "fps": fps,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "container": (payload.get("format") or {}).get("format_name"),
    }


def resolve_library_media(library_service: Any, media_item_id: str, asset_id: Optional[str] = None) -> tuple[dict[str, Any], Path]:
    """Resolve an immutable library source.

    The current library stores one canonical source per item. ``asset_id`` is
    accepted for forward compatibility but must either be absent or match a
    registered artifact whose library item can be resolved by the caller.
    """
    if library_service is None:
        raise RuntimeError("library service is not configured")
    meta = library_service.get_item(media_item_id)
    path = library_service.item_file_path(media_item_id)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"media item has no readable source: {media_item_id}")
    return meta, path


def register_derived_library_item(
    library_service: Any,
    *,
    output_path: Path,
    title: str,
    duration_seconds: float,
    operation: str,
    source_media_item_id: str,
    artifact_id: str,
    container: str,
    metadata: dict[str, Any],
) -> tuple[str, Path]:
    if library_service is None:
        raise RuntimeError("library service is not configured")
    container = sanitize_container(container.lower())
    canonical_name = f"source.{container}"
    item = library_service.create_upload_item(
        file_name=canonical_name,
        title=title,
        duration_seconds=duration_seconds,
    )
    item_id = item["id"]
    dest = library_service.storage.source_file_path(item_id, container)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(output_path), str(dest))
    except Exception:
        try:
            library_service.delete_item(item_id)
        except Exception:
            pass
        raise
    item["file_name"] = canonical_name
    item["container"] = container
    item["file_size_bytes"] = dest.stat().st_size
    item["duration_seconds"] = duration_seconds
    item["derived"] = True
    item["derived_from_item_id"] = source_media_item_id
    item["derivation"] = {
        "operation": operation,
        "artifact_id": artifact_id,
        **metadata,
    }
    artifacts = item.setdefault("artifacts", [])
    artifacts.append({
        "artifact_id": artifact_id,
        "artifact_type": operation,
        "created_at": now_iso(),
        "revision": "1",
    })
    item["updated_at"] = now_iso()
    library_service.storage.save_item(item)
    return item_id, dest
