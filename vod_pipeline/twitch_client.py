"""Twitch channel lister via yt-dlp (no API credentials needed).

Replaces the former Helix API client. yt-dlp handles Twitch's GraphQL
internally, so we can list channel VODs, clips and fetch single-video
metadata without any Client-ID / Client-Secret / OAuth flow.

All methods run ``yt-dlp`` as a subprocess and parse its JSON-per-line
output. No network calls happen from the FastAPI process directly.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import urllib.request
from typing import Any, Optional

from .schemas import TwitchClientError, TwitchNotFoundError

logger = logging.getLogger("ttvturbo.vod_pipeline.channel_lister")

YTDLP_BIN = "yt-dlp"
DEFAULT_TIMEOUT_SECONDS = 120.0

# Twitch's public web client-id (used by the twitch.tv website itself).
# This lets us query the GQL endpoint for channel metadata (display name,
# avatar) without needing our own OAuth credentials.
TWITCH_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_GQL_URL = "https://gql.twitch.tv/gql"


def _ytdlp_available() -> bool:
    return shutil.which(YTDLP_BIN) is not None


class ChannelLister:
    """List Twitch channel VODs + clips and fetch single-video metadata.

    Uses the ``yt-dlp`` CLI (not the Python API) so listing never imports
    yt-dlp into the FastAPI process and a hanging listing can be killed
    via subprocess timeout.
    """

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = float(timeout_seconds)

    # ------------------------------------------------------------------ helpers
    def _run_ytdlp(self, args: list[str]) -> list[dict]:
        """Run yt-dlp with ``--flat-playlist --dump-json`` and parse output.

        Returns a list of parsed JSON entry dicts. Raises
        :class:`TwitchClientError` on failure.
        """
        if not _ytdlp_available():
            raise TwitchClientError("yt-dlp is not installed or not on PATH")
        cmd = [YTDLP_BIN, "--flat-playlist", "--dump-json", *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise TwitchClientError(
                f"yt-dlp listing timed out after {self.timeout_seconds:.0f}s"
            ) from exc
        except OSError as exc:
            raise TwitchClientError(f"Could not run yt-dlp: {exc}") from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            # yt-dlp returns non-zero if the channel has no VODs or doesn't exist.
            if "does not exist" in stderr.lower() or "not found" in stderr.lower():
                raise TwitchNotFoundError(stderr or "Twitch channel not found")
            raise TwitchClientError(
                f"yt-dlp exited with code {proc.returncode}: {stderr[:500]}"
            )
        entries: list[dict] = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def _run_ytdlp_single(self, url: str) -> dict:
        """Run ``yt-dlp --dump-json --skip-download`` for a single URL.

        Returns the parsed info dict. Raises :class:`TwitchNotFoundError`
        if the video does not exist, :class:`TwitchClientError` on other
        failures.
        """
        if not _ytdlp_available():
            raise TwitchClientError("yt-dlp is not installed or not on PATH")
        cmd = [YTDLP_BIN, "--dump-json", "--skip-download", url]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise TwitchClientError(
                f"yt-dlp metadata fetch timed out after {self.timeout_seconds:.0f}s"
            ) from exc
        except OSError as exc:
            raise TwitchClientError(f"Could not run yt-dlp: {exc}") from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if "does not exist" in stderr.lower() or "not found" in stderr.lower() or "no video" in stderr.lower():
                raise TwitchNotFoundError(stderr or "Twitch video not found")
            raise TwitchClientError(
                f"yt-dlp exited with code {proc.returncode}: {stderr[:500]}"
            )
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        raise TwitchClientError("yt-dlp produced no JSON output")

    # ------------------------------------------------------------------ listing
    def list_vods(self, login: str, limit: int = 100) -> list[dict]:
        """List VODs for a Twitch channel via ``yt-dlp --flat-playlist``.

        Returns a list of normalized entry dicts with keys: ``id``,
        ``url``, ``title``, ``thumbnail``, ``duration``, ``view_count``,
        ``timestamp``, ``type`` (always ``"archive"``).
        """
        url = f"https://www.twitch.tv/{login}/videos"
        entries = self._run_ytdlp([
            "--playlist-end", str(max(1, int(limit))),
            url,
        ])
        return [_normalize_flat_entry(e, "archive") for e in entries]

    def list_clips(self, login: str, limit: int = 100) -> list[dict]:
        """List clips for a Twitch channel via ``yt-dlp --flat-playlist``."""
        url = f"https://www.twitch.tv/{login}/clips"
        entries = self._run_ytdlp([
            "--playlist-end", str(max(1, int(limit))),
            url,
        ])
        return [_normalize_flat_entry(e, "clip") for e in entries]

    def get_video_info(self, url: str) -> dict:
        """Fetch metadata for a single VOD or clip URL.

        Returns a normalized dict with keys: ``id``, ``url``, ``title``,
        ``thumbnail``, ``duration``, ``view_count``, ``timestamp``,
        ``uploader``, ``type`` (``"archive"`` or ``"clip"``).
        """
        info = self._run_ytdlp_single(url)
        return _normalize_single_info(info)

    def get_channel_info(self, login: str) -> dict:
        """Fetch channel metadata (display name, avatar URL).

        Uses the Twitch GQL endpoint (public web client-id) which works
        for both live and offline channels. Falls back to yt-dlp if the
        GQL call fails.

        Returns a dict with keys: ``login``, ``display_name``,
        ``avatar_url``, ``channel_url``. Best-effort — missing fields
        are empty strings. Raises :class:`TwitchNotFoundError` if the
        channel does not exist.
        """
        url = f"https://www.twitch.tv/{login}"
        # Try the Twitch GQL endpoint first — it works for offline channels
        # (unlike yt-dlp's twitch:stream extractor which only works when live).
        try:
            gql_resp = self._gql_channel_info(login)
            if gql_resp:
                return gql_resp
        except Exception as exc:
            logger.debug("GQL channel info failed for %s: %s", login, exc)
        # Fallback: yt-dlp (only works when the channel is live).
        info = self._run_ytdlp_single(url)
        return {
            "login": str(info.get("uploader_id") or info.get("channel_id") or login or ""),
            "display_name": str(info.get("uploader") or info.get("channel") or login or ""),
            "avatar_url": str(info.get("thumbnails", [{}])[0].get("url", "") if info.get("thumbnails") else (info.get("thumbnail") or "")),
            "channel_url": str(info.get("webpage_url") or url or ""),
        }

    def _gql_channel_info(self, login: str) -> Optional[dict]:
        """Query Twitch GQL for channel display name + avatar.

        Returns a dict with the same keys as :meth:`get_channel_info`,
        or ``None`` if the channel was not found.
        """
        query = {
            "query": (
                '{user(login:"%s"){displayName profileImageURL(width:300) login}}'
                % login.replace('"', "")
            )
        }
        req = urllib.request.Request(
            TWITCH_GQL_URL,
            data=json.dumps(query).encode("utf-8"),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Client-Id": TWITCH_WEB_CLIENT_ID,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        user = (payload.get("data") or {}).get("user")
        if not user:
            return None
        avatar = str(user.get("profileImageURL") or "")
        return {
            "login": str(user.get("login") or login or ""),
            "display_name": str(user.get("displayName") or login or ""),
            "avatar_url": avatar,
            "channel_url": f"https://www.twitch.tv/{login}",
        }


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_flat_entry(entry: dict, default_type: str) -> dict:
    """Normalize a yt-dlp ``--flat-playlist`` entry into a stable dict."""
    ie_key = str(entry.get("ie_key", "")).lower()
    if "clip" in ie_key:
        vod_type = "clip"
    else:
        vod_type = default_type
    return {
        "id": str(entry.get("id", "")),
        "url": str(entry.get("url", "")),
        "title": str(entry.get("title", "") or ""),
        "thumbnail": str(entry.get("thumbnail", "") or ""),
        "duration": entry.get("duration"),
        "view_count": entry.get("view_count"),
        "timestamp": entry.get("timestamp"),
        "upload_date": entry.get("upload_date"),
        "type": vod_type,
    }


def _normalize_single_info(info: dict) -> dict:
    """Normalize a yt-dlp ``--dump-json`` info dict into a stable dict."""
    extractor = str(info.get("extractor_key", "")).lower()
    if "clip" in extractor:
        vod_type = "clip"
    else:
        vod_type = "archive"
    return {
        "id": str(info.get("id", "")),
        "url": str(info.get("webpage_url", info.get("url", ""))),
        "title": str(info.get("title", "") or ""),
        "thumbnail": str(info.get("thumbnail", "") or ""),
        "duration": info.get("duration"),
        "view_count": info.get("view_count"),
        "timestamp": info.get("timestamp"),
        "upload_date": info.get("upload_date"),
        "uploader": str(info.get("uploader", info.get("channel", "")) or ""),
        "channel": str(info.get("channel", "") or ""),
        "type": vod_type,
    }
