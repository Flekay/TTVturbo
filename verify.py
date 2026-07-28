"""Automated verification for the TTVturbo minimal recording app.

Checks:
  1. FastAPI starts (import + TestClient).
  2. Start page (GET /) loads and contains the expected buttons.
  3. Upload endpoint (POST /api/recordings) accepts a real audio file.
  4. FFmpeg produces a valid, playable WAV (verified via ffprobe).
  5. The stored WAV is retrievable via GET /api/recordings/{filename}.
  6. GET /api/recordings lists WAVs with correct metadata, newest first.
  7. Non-WAV files are ignored; corrupted WAVs do not crash the server.
  8. DELETE /api/recordings/{filename} removes the real file (404 if missing).
  9. Path traversal like ../example.wav is blocked for DELETE.

Run:  python verify.py
Exit code is 0 on success, non-zero on any failure.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from app_factory import create_app
from settings import Settings

BASE_DIR = Path(__file__).resolve().parent
_settings = Settings.from_env()
RECORDINGS_DIR = _settings.paths().recordings


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
    app = create_app(_settings)
    try:
        with TestClient(app) as client:
            _run_verification_checks(client)
    except Exception as exc:  # noqa: BLE001
        fail(f"FastAPI app could not be constructed: {exc}")


def _run_verification_checks(client: TestClient) -> None:
    # 2. Start page loads. The React dashboard is served from frontend/dist
    #    when built; otherwise the legacy static test page is served.
    resp = client.get("/")
    if resp.status_code != 200:
        fail(f"GET / returned {resp.status_code}.")
    body = resp.text
    if "id=\"root\"" not in body and "Aufnahme starten" not in body:
        fail("Start page missing expected content (neither React root nor legacy UI).")
    ok("Start page (GET /) loads and serves a frontend.")

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

    # 6-9. Phase 2: persistent recordings list + delete.
    phase2_checks(client, wav_name)


def phase2_checks(client: TestClient, uploaded_wav: str) -> None:
    """Run the Phase 2 verification suite against the recordings API."""

    # Prepare an isolated set of test files inside RECORDINGS_DIR.
    # Use unique names so we don't collide with existing recordings.
    import time
    stamp = int(time.time() * 1000)

    # Two real WAV files with known durations (1s and 2s).
    wav_a = RECORDINGS_DIR / f"verify_a_{stamp}.wav"
    wav_b = RECORDINGS_DIR / f"verify_b_{stamp}.wav"
    make_real_wav(wav_a, duration=1.0)
    make_real_wav(wav_b, duration=2.0)

    # A non-WAV file that must be ignored.
    non_wav = RECORDINGS_DIR / f"verify_ignore_{stamp}.txt"
    non_wav.write_text("not audio", encoding="utf-8")

    # A corrupted WAV (bad header) that must not crash the server.
    corrupt = RECORDINGS_DIR / f"verify_corrupt_{stamp}.wav"
    corrupt.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt notreallywav")

    created = []
    try:
        # 1) Two WAV files are listed correctly.
        resp = client.get("/api/recordings")
        if resp.status_code != 200:
            fail(f"GET /api/recordings returned {resp.status_code}: {resp.text}")
        body = resp.json()
        recs = body.get("recordings", [])
        names = [r["filename"] for r in recs]
        if wav_a.name not in names or wav_b.name not in names:
            fail(f"Recordings list missing test WAVs: {names}")
        ok(f"GET /api/recordings lists {len(recs)} recording(s), both test WAVs present.")

        # 2) Non-WAV file is ignored.
        if non_wav.name in names:
            fail(f"Non-WAV file {non_wav.name} should be ignored.")
        ok("Non-WAV file is ignored.")

        # 3) Corrupted WAV is skipped and does not crash the server.
        if corrupt.name in names:
            fail(f"Corrupted WAV {corrupt.name} should be skipped.")
        ok("Corrupted WAV is skipped without server crash.")

        # 4) Duration is read from the real WAV file (not the filename).
        rec_a = next(r for r in recs if r["filename"] == wav_a.name)
        rec_b = next(r for r in recs if r["filename"] == wav_b.name)
        if not (0.9 <= rec_a["duration_seconds"] <= 1.1):
            fail(f"Duration for 1s WAV wrong: {rec_a['duration_seconds']}")
        if not (1.9 <= rec_b["duration_seconds"] <= 2.1):
            fail(f"Duration for 2s WAV wrong: {rec_b['duration_seconds']}")
        ok(f"Durations read from real WAV: a={rec_a['duration_seconds']}s, b={rec_b['duration_seconds']}s.")

        # Verify required fields exist for each recording.
        for r in (rec_a, rec_b):
            for field in ("filename", "created_at", "duration_seconds",
                          "file_size_bytes", "audio_url"):
                if field not in r:
                    fail(f"Recording entry missing field {field!r}: {r}")
            if r["audio_url"] != f"/api/recordings/{r['filename']}":
                fail(f"audio_url mismatch: {r['audio_url']}")
        ok("All required metadata fields are present and audio_url is correct.")

        # 5) Ordering: newest first. We touch wav_b to be newer than wav_a.
        import os as _os
        newer_mtime = _os.path.getmtime(wav_a) + 10
        _os.utime(wav_b, (newer_mtime, newer_mtime))
        resp = client.get("/api/recordings")
        recs2 = resp.json()["recordings"]
        idx_a = next(i for i, r in enumerate(recs2) if r["filename"] == wav_a.name)
        idx_b = next(i for i, r in enumerate(recs2) if r["filename"] == wav_b.name)
        if idx_b >= idx_a:
            fail(f"Newer recording (b) should come before older (a): idx_b={idx_b}, idx_a={idx_a}")
        ok("Ordering is newest first.")

        # 6) DELETE removes the real file.
        resp = client.delete(f"/api/recordings/{wav_a.name}")
        if resp.status_code != 200:
            fail(f"DELETE returned {resp.status_code}: {resp.text}")
        if wav_a.is_file():
            fail(f"File {wav_a.name} still exists after DELETE.")
        ok(f"DELETE removed {wav_a.name} from disk.")

        # 7) Deleting an unknown file returns 404.
        resp = client.delete(f"/api/recordings/does_not_exist_{stamp}.wav")
        if resp.status_code != 404:
            fail(f"DELETE unknown file should return 404, got {resp.status_code}.")
        ok("DELETE on unknown file returns 404.")

        # 8) Path traversal is blocked.
        for bad in ("../example.wav", "..\\example.wav", "/etc/passwd",
                    "subdir/../example.wav", ".hidden.wav", "noext",
                    "weird.WAV.txt"):
            resp = client.delete(f"/api/recordings/{bad}")
            if resp.status_code == 200:
                fail(f"DELETE accepted dangerous path: {bad}")
        ok("Path traversal and invalid names are blocked on DELETE.")

        # 9) The previously uploaded recording is also listed.
        resp = client.get("/api/recordings")
        names_final = [r["filename"] for r in resp.json()["recordings"]]
        if uploaded_wav not in names_final:
            fail(f"Previously uploaded recording {uploaded_wav} not listed.")
        ok("Previously uploaded recording appears in the list.")

    finally:
        for p in (wav_a, wav_b, non_wav, corrupt):
            try:
                p.unlink()
            except OSError:
                pass

    print("\nAll automated checks passed.")
    print("NOTE: Real browser microphone test was NOT executed in this run")
    print("      (no microphone available in this automated environment).")
    print("      Manual browser test: open http://127.0.0.1:8000, allow mic,")
    print("      record, stop, and play back the result. The list below the")
    print("      recorder should show all stored recordings and allow playback")
    print("      and deletion.")


def make_real_wav(path: Path, duration: float = 1.0) -> None:
    """Write a real, valid PCM WAV file with the given duration in seconds."""
    sample_rate = 44100
    n_frames = int(sample_rate * duration)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        # Simple sine-ish bytes; content does not matter, only the header.
        frames = bytes([(i & 0xFF) for i in range(n_frames * 2)])
        wav.writeframes(frames)
    if not path.is_file() or path.stat().st_size == 0:
        fail(f"Could not write test WAV at {path}.")


if __name__ == "__main__":
    main()
