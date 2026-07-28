"""Executable resolution for FFmpeg, FFprobe, yt-dlp and friends.

This module is the **single neutral location** for finding external tools.
Domain modules (``media_processing``, ``vod_pipeline``, …) import from
here instead of from the app entrypoint, so the dependency direction is::

    App Factory → Settings / ExecutableResolver → Service

Never::

    Service → App Factory

``find_executable(name)`` tries ``shutil.which`` first, then falls back to
common Windows install locations (winget, Program Files, ``C:\\ffmpeg``)
so the app still works in shells whose PATH was not refreshed after
installing FFmpeg.

``resolve_tools(settings)`` returns a validated :class:`ExecutableResolver`
that caches the resolved paths for FFmpeg / FFprobe / yt-dlp so services
do not repeat the lookup on every request.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ttvturbo.settings import Settings


def find_executable(name: str) -> str | None:
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
        candidates: list[Path] = []
        if local_app:
            candidates.append(
                Path(local_app) / "Microsoft" / "WinGet" / "Packages"
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


@dataclass(frozen=True)
class ExecutableResolver:
    """Resolved tool paths, ready to inject into services.

    Each field is either an absolute path string or ``None`` when the tool
    was not found.  Services receive the resolved value via constructor
    parameters and never call ``shutil.which`` themselves.
    """

    ffmpeg: Optional[str]
    ffprobe: Optional[str]
    yt_dlp: Optional[str]
    python: str

    def is_available(self, tool: str) -> bool:
        """True if the named tool (``ffmpeg``/``ffprobe``/``yt_dlp``) was found."""
        return getattr(self, tool) is not None


def resolve_tools(settings: Settings) -> ExecutableResolver:
    """Build an :class:`ExecutableResolver` from *settings*.

    Explicit paths in ``settings`` (``ffmpeg_path``, ``ffprobe_path``,
    ``yt_dlp``) take precedence; otherwise ``find_executable`` is used as
    a PATH + Windows-fallback lookup.
    """
    ffmpeg = settings.ffmpeg_path or find_executable("ffmpeg")
    ffprobe = settings.ffprobe_path or find_executable("ffprobe")
    yt_dlp = settings.yt_dlp or find_executable("yt-dlp")
    python = settings.worker_python
    return ExecutableResolver(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        yt_dlp=yt_dlp,
        python=python,
    )
