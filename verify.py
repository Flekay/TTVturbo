"""Automated verification for the TTVturbo minimal recording app.

Checks:
  1. FastAPI starts (import + TestClient).
  2. Start page (GET /) loads and contains the expected buttons.
  3. Upload endpoint (POST /api/recordings) accepts a real audio file.
  4. FFmpeg produces a valid, playable WAV (verified via ffprobe).
  5. The stored WAV is retrievable via GET /api/recordings/{filename}.

Run:  python verify.py
Exit code is 0 on success, non-zero on any failure.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module

BASE_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = app_module.RECORDINGS_DIR


def fail(msg: str) -> None:
    print("FAIL:", msg)
    sys.exit(1)


def ok(msg: str) -> None:
    print("OK  :", msg)


def make_test_audio(out_path: Path) -> None:
    """Generate a real 2-second sine wave audio file with FFmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        fail("ffmpeg not on PATH; cannot generate test audio.")
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=2",
        "-acodec", "libopus",
        out_path.as_posix(),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not out_path.is_file():
        fail("Could not generate test audio with ffmpeg: "
             + proc.stderr.decode("utf-8", errors="replace")[-1000:])


def main() -> None:
    # 0. Pre-checks
    if shutil.which("ffmpeg") is None:
        fail("ffmpeg not found on PATH.")
    if shutil.which("ffprobe") is None:
        fail("ffprobe not found on PATH.")
    ok("ffmpeg and ffprobe available.")

    # 1. FastAPI starts (TestClient builds the app without a network).
    try:
        client = TestClient(app_module.app)
    except Exception as exc:  # noqa: BLE001
        fail(f"FastAPI app could not be constructed: {exc}")
    ok("FastAPI app constructs.")

    # 2. Start page loads.
    resp = client.get("/")
    if resp.status_code != 200:
        fail(f"GET / returned {resp.status_code}.")
    body = resp.text
    for needle in ["Aufnahme starten", "Aufnahme stoppen", "app.js", "style.css"]:
        if needle not in body:
            fail(f"Start page missing expected content: {needle!r}.")
    ok("Start page (GET /) loads and contains required UI elements.")

    # 3. Upload endpoint accepts a real audio file.
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "recording.webm"
        make_test_audio(src)
        if src.stat().st_size == 0:
            fail("Generated test audio is empty.")
        ok(f"Generated real test audio ({src.stat().st_size} bytes).")

        with src.open("rb") as fh:
            resp = client.post(
                "/api/recordings",
                files={"audio": (src.name, fh, "audio/webm")},
            )
        if resp.status_code != 201:
            fail(f"POST /api/recordings returned {resp.status_code}: {resp.text}")
        data = resp.json()
        if "filename" not in data or "url" not in data:
            fail(f"Upload response missing fields: {data}")
        wav_name = data["filename"]
        wav_url = data["url"]
        if not wav_name.endswith(".wav"):
            fail(f"Returned filename is not a WAV: {wav_name}")
        ok(f"Upload accepted; server returned {wav_name} ({data.get('size_bytes')} bytes).")

    # 4. FFmpeg produced a valid, playable WAV (ffprobe).
    wav_path = RECORDINGS_DIR / wav_name
    if not wav_path.is_file():
        fail(f"WAV file not stored at {wav_path}.")
    ffprobe = shutil.which("ffprobe")
    probe = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,channels,sample_rate,duration",
            "-of", "default=noprint_wrappers=1",
            wav_path.as_posix(),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if probe.returncode != 0:
        fail("ffprobe could not read the WAV: "
             + probe.stderr.decode("utf-8", errors="replace")[-1000:])
    probe_out = probe.stdout.decode("utf-8", errors="replace").strip()
    if "codec_name=pcm_s16le" not in probe_out:
        fail(f"WAV is not PCM s16le: {probe_out}")
    if "sample_rate=44100" not in probe_out:
        fail(f"WAV sample rate not 44100: {probe_out}")
    if "channels=1" not in probe_out:
        fail(f"WAV is not mono: {probe_out}")
    ok(f"FFmpeg produced a valid WAV. Probe: {probe_out.replace(chr(10), ' ')}")

    # 5. The stored WAV is retrievable.
    resp = client.get(wav_url)
    if resp.status_code != 200:
        fail(f"GET {wav_url} returned {resp.status_code}.")
    if not resp.content.startswith(b"RIFF") or b"WAVE" not in resp.content[:12]:
        fail("Retrieved file does not have a RIFF/WAVE header.")
    ok(f"GET {wav_url} returns a valid WAV ({len(resp.content)} bytes).")

    print("\nAll automated checks passed.")
    print("NOTE: Real browser microphone test was NOT executed in this run")
    print("      (no microphone available in this automated environment).")
    print("      Manual browser test: open http://127.0.0.1:8000, allow mic,")
    print("      record, stop, and play back the result.")


if __name__ == "__main__":
    main()
