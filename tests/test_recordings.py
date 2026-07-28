"""Tests for the recordings API (upload, list, get, delete)."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


def test_list_recordings_returns_metadata(client, isolated_recordings):
    resp = client.get("/api/recordings")
    assert resp.status_code == 200
    recs = resp.json()["recordings"]
    names = [r["filename"] for r in recs]
    assert isolated_recordings["a"].name in names
    assert isolated_recordings["b"].name in names
    for r in recs:
        for field in ("filename", "created_at", "duration_seconds",
                      "file_size_bytes", "audio_url"):
            assert field in r
        assert r["audio_url"] == f"/api/recordings/{r['filename']}"


def test_list_recordings_newest_first(client, isolated_recordings, recordings_dir):
    import os as _os
    a = isolated_recordings["a"]
    b = isolated_recordings["b"]
    # Make b newer than a.
    newer = _os.path.getmtime(a) + 10
    _os.utime(b, (newer, newer))
    recs = client.get("/api/recordings").json()["recordings"]
    idx_a = next(i for i, r in enumerate(recs) if r["filename"] == a.name)
    idx_b = next(i for i, r in enumerate(recs) if r["filename"] == b.name)
    assert idx_b < idx_a


def test_list_recordings_ignores_non_wav(client, recordings_dir):
    stamp = int(time.time() * 1000)
    non_wav = recordings_dir / f"ignore_{stamp}.txt"
    non_wav.write_text("not audio", encoding="utf-8")
    try:
        recs = client.get("/api/recordings").json()["recordings"]
        assert non_wav.name not in [r["filename"] for r in recs]
    finally:
        non_wav.unlink(missing_ok=True)


def test_list_recordings_skips_corrupted_wav(client, recordings_dir):
    stamp = int(time.time() * 1000)
    corrupt = recordings_dir / f"corrupt_{stamp}.wav"
    corrupt.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt notreallywav")
    try:
        recs = client.get("/api/recordings").json()["recordings"]
        assert corrupt.name not in [r["filename"] for r in recs]
    finally:
        corrupt.unlink(missing_ok=True)


def test_get_recording_streams_wav(client, isolated_recordings):
    a = isolated_recordings["a"]
    resp = client.get(f"/api/recordings/{a.name}")
    assert resp.status_code == 200
    assert resp.content.startswith(b"RIFF")
    assert b"WAVE" in resp.content[:12]


def test_get_recording_404_when_missing(client):
    resp = client.get("/api/recordings/does_not_exist_xyz.wav")
    assert resp.status_code == 404


def test_get_recording_rejects_path_traversal(client):
    resp = client.get("/api/recordings/..%2Fexample.wav")
    # FastAPI decodes the path; the route must reject traversal.
    assert resp.status_code in (400, 404)


def test_upload_recording_converts_to_wav(client, make_test_audio, tmp_path, recordings_dir):
    src = tmp_path / "rec.webm"
    make_test_audio(src)
    assert src.stat().st_size > 0
    with src.open("rb") as fh:
        resp = client.post(
            "/api/recordings",
            files={"audio": (src.name, fh, "audio/webm")},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["filename"].endswith(".wav")
    wav_path = recordings_dir / data["filename"]
    try:
        assert wav_path.is_file()
        # Verify the produced WAV is real and playable via ffprobe.
        ffprobe = shutil.which("ffprobe")
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,channels,sample_rate",
             "-of", "default=noprint_wrappers=1", str(wav_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert probe.returncode == 0
        out = probe.stdout.decode("utf-8", errors="replace")
        assert "codec_name=pcm_s16le" in out
        assert "sample_rate=44100" in out
        assert "channels=1" in out
    finally:
        wav_path.unlink(missing_ok=True)


def test_upload_recording_rejects_bad_extension(client):
    resp = client.post(
        "/api/recordings",
        files={"audio": ("file.exe", b"binary", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_upload_recording_rejects_empty(client):
    resp = client.post(
        "/api/recordings",
        files={"audio": ("rec.webm", b"", "audio/webm")},
    )
    # Either 400 (empty after read) or 413/415; here we expect 400.
    assert resp.status_code == 400


def test_delete_recording_removes_file(client, isolated_recordings):
    a = isolated_recordings["a"]
    resp = client.delete(f"/api/recordings/{a.name}")
    assert resp.status_code == 200
    assert not a.is_file()


def test_delete_recording_404_when_missing(client):
    resp = client.delete("/api/recordings/does_not_exist_xyz.wav")
    assert resp.status_code == 404


def test_delete_recording_blocks_path_traversal(client):
    for bad in ("../example.wav", "..\\example.wav", "/etc/passwd",
                "subdir/../example.wav", ".hidden.wav", "noext",
                "weird.WAV.txt"):
        resp = client.delete(f"/api/recordings/{bad}")
        assert resp.status_code != 200, f"accepted dangerous path: {bad}"
