"""Tests for the VOD download worker orchestration.

These tests never run a real yt-dlp download. They monkeypatch the worker
subprocess to run a tiny inline Python script that writes a real
(FFprobe-verifiable) MP4 into the VOD directory, then exits with a
controlled code. This exercises the real reaper, progress persistence,
cancel, retry, restart-recovery and FFprobe verification paths.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from vod_pipeline import VodStatus
from vod_pipeline.service import FFprobeError, ffprobe_inspect


def _write_fake_worker_script(tmp_path: Path, exit_code: int = 0, delay: float = 0.0, fail_verify: bool = False) -> Path:
    """Write a standalone Python script that mimics the real worker.

    It writes a real MP4 (via ffmpeg) into the VOD dir, sets the metadata
    to VERIFYING with the final file_name, then exits with ``exit_code``.
    """
    script = tmp_path / "fake_worker.py"
    fail_verify_str = "True" if fail_verify else "False"
    script.write_text(textwrap.dedent(f"""
        import json, os, sys, time, shutil, subprocess
        job = json.load(open(sys.argv[1], encoding="utf-8"))
        out_dir = job["output_dir"]
        meta_path = job["metadata_path"]
        if {delay}:
            time.sleep({delay})

        def write_meta(m):
            tmp = meta_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(m, fh)
                fh.flush()
            for _ in range(10):
                try:
                    os.replace(tmp, meta_path)
                    return
                except PermissionError:
                    time.sleep(0.05)

        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["status"] = "DOWNLOADING"
        meta["progress"] = {{"percent": 50.0, "downloaded_bytes": 100, "total_bytes": 200, "speed_bytes_per_second": 1000.0, "eta_seconds": 1.0}}
        meta["updated_at"] = "2024-01-01T00:00:00+00:00"
        write_meta(meta)
        target = os.path.join(out_dir, "source.mp4")
        if not {fail_verify_str}:
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dl = dict(meta.get("download") or {{}})
        dl["file_name"] = "source.mp4"
        if os.path.isfile(target):
            dl["file_size_bytes"] = os.path.getsize(target)
        dl["container"] = "mp4"
        meta["download"] = dl
        meta["status"] = "VERIFYING"
        meta["progress"] = {{"percent": None, "downloaded_bytes": None, "total_bytes": None, "speed_bytes_per_second": None, "eta_seconds": None}}
        write_meta(meta)
        sys.exit({exit_code})
    """), encoding="utf-8")
    return script


def _patch_spawn(service, script_path: Path):
    """Replace _spawn_worker to run the fake script instead of the real one."""
    orig = service._spawn_worker

    def fake_spawn(vod_id, vod):
        vod_dir = service._vod_dir(vod_id)
        vod_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = service.storage._vod_path(vod_id)
        job = {
            "vod_id": vod_id,
            "source_url": vod.get("source_url", ""),
            "output_dir": str(vod_dir),
            "metadata_path": str(metadata_path),
        }
        job_path = vod_dir / "job.json"
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        log_path = service.storage.vod_worker_log_path(vod_id)
        log_fh = open(log_path, "wb", buffering=0)
        cmd = [sys.executable, str(script_path), str(job_path)]
        proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        with service._lock:
            service._active[vod_id] = proc
            service._active_log_fh[vod_id] = log_fh
        import threading
        reaper = threading.Thread(
            target=service._reap_worker,
            args=(vod_id, proc, log_fh),
            daemon=True,
            name=f"vod-reaper-{vod_id}",
        )
        reaper.start()

    service._spawn_worker = fake_spawn


def _wait_for_status(service, vod_id, target_statuses, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        vod = service._read_vod(vod_id)
        if vod is not None and vod.get("status") in target_statuses:
            return vod
        time.sleep(0.1)
    vod = service._read_vod(vod_id)
    return vod


@pytest.fixture()
def vod_with_profile(vod_service, channel_lister):
    channel_lister.add_vod("casepayt", "100")
    profile = vod_service.create_profile("casepayt")
    vod_service.sync_vods(profile["id"])
    vod = vod_service.list_vods(profile_id=profile["id"])[0]
    return vod


def test_start_download_marks_queued_then_ready(vod_service, vod_with_profile, tmp_path, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed for real verification")
    script = _write_fake_worker_script(tmp_path, exit_code=0)
    _patch_spawn(vod_service, script)
    vod = vod_service.start_download(vod_with_profile["id"])
    assert vod["status"] in (VodStatus.QUEUED.value, VodStatus.DOWNLOADING.value)
    final = _wait_for_status(
        vod_service, vod_with_profile["id"],
        {VodStatus.READY.value, VodStatus.FAILED.value},
        timeout=30.0,
    )
    assert final is not None
    assert final["status"] == VodStatus.READY.value, final
    assert final["download"]["file_name"] == "source.mp4"
    assert final["download"]["file_size_bytes"] > 0
    assert final["download"]["video_codec"]
    assert final["download"]["audio_codec"]


def test_second_parallel_start_blocked(vod_service, vod_with_profile, tmp_path, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed")
    script = _write_fake_worker_script(tmp_path, exit_code=0, delay=2.0)
    _patch_spawn(vod_service, script)
    vod_service.start_download(vod_with_profile["id"])
    # Need a second VOD to attempt a parallel start.
    lister = vod_service.lister
    lister.add_vod("casepayt", "101")
    profile_id = vod_with_profile["profile_id"]
    vod_service.sync_vods(profile_id)
    second = [v for v in vod_service.list_vods(profile_id=profile_id) if v["id"] != vod_with_profile["id"]][0]
    from vod_pipeline import VodConflictError
    with pytest.raises(VodConflictError):
        vod_service.start_download(second["id"])
    # Cleanup the running one.
    vod_service.cancel_download(vod_with_profile["id"])


def test_start_download_auto_recovers_orphaned_downloading(vod_service, vod_with_profile):
    """If a VOD is stuck in DOWNLOADING but no worker is tracked (orphan
    after server restart), start_download auto-recovers it to FAILED and
    proceeds instead of locking the user out with a permanent 409."""
    vod = vod_service.get_vod(vod_with_profile["id"])
    vod["status"] = VodStatus.DOWNLOADING.value
    vod["progress"] = {"percent": 50.0, "downloaded_bytes": 100, "total_bytes": 200, "speed_bytes_per_second": 1.0, "eta_seconds": 1.0}
    vod_service.storage.save_vod(vod)
    # No worker in _active — simulates a post-restart orphan.
    assert vod_with_profile["id"] not in vod_service._active
    # start_download should recover and re-queue (but we don't have a real
    # worker, so it'll just transition to QUEUED).
    result = vod_service.start_download(vod_with_profile["id"])
    assert result["status"] == VodStatus.QUEUED.value
    # Cancel to clean up.
    vod_service.cancel_download(vod_with_profile["id"])


def test_worker_exit_one_marks_failed(vod_service, vod_with_profile, tmp_path, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed")
    # exit 1 but the fake worker already set VERIFYING; the reaper will run
    # FFprobe on a real file -> READY. To force FAILED, use fail_verify so
    # no file is produced, then exit 1.
    script = _write_fake_worker_script(tmp_path, exit_code=1, fail_verify=True)
    _patch_spawn(vod_service, script)
    vod_service.start_download(vod_with_profile["id"])
    final = _wait_for_status(
        vod_service, vod_with_profile["id"],
        {VodStatus.READY.value, VodStatus.FAILED.value},
        timeout=30.0,
    )
    assert final["status"] == VodStatus.FAILED.value
    assert final["error"]


def test_cancel_download(vod_service, vod_with_profile, tmp_path, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed")
    script = _write_fake_worker_script(tmp_path, exit_code=0, delay=10.0)
    _patch_spawn(vod_service, script)
    vod_service.start_download(vod_with_profile["id"])
    # Wait until DOWNLOADING.
    _wait_for_status(vod_service, vod_with_profile["id"], {VodStatus.DOWNLOADING.value}, timeout=10.0)
    vod = vod_service.cancel_download(vod_with_profile["id"])
    assert vod["status"] == VodStatus.CANCELED.value
    # No source file should remain for a canceled VOD.
    src = vod_service._source_path_for(vod)
    assert src is None


def test_retry_after_failure(vod_service, vod_with_profile, tmp_path, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed")
    # First attempt fails (no file, exit 1).
    bad = _write_fake_worker_script(tmp_path, exit_code=1, fail_verify=True)
    _patch_spawn(vod_service, bad)
    vod_service.start_download(vod_with_profile["id"])
    final = _wait_for_status(
        vod_service, vod_with_profile["id"],
        {VodStatus.FAILED.value, VodStatus.READY.value},
        timeout=30.0,
    )
    assert final["status"] == VodStatus.FAILED.value
    # Now patch with a good worker and retry.
    good = _write_fake_worker_script(tmp_path, exit_code=0)
    _patch_spawn(vod_service, good)
    vod = vod_service.retry_download(vod_with_profile["id"])
    final = _wait_for_status(
        vod_service, vod_with_profile["id"],
        {VodStatus.READY.value, VodStatus.FAILED.value},
        timeout=30.0,
    )
    assert final["status"] == VodStatus.READY.value


def test_retry_only_for_failed_or_canceled(vod_service, vod_with_profile):
    # DISCOVERED -> retry not allowed; use start_download instead.
    from vod_pipeline import VodConflictError
    with pytest.raises(VodConflictError):
        vod_service.retry_download(vod_with_profile["id"])


def test_restart_recovery_marks_transient_failed(vod_data_dir, vod_download_dir, channel_lister, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed")
    from vod_pipeline import VodPipelineStorage
    from vod_pipeline.service import VodPipelineService
    storage = VodPipelineStorage(vod_data_dir)
    svc1 = VodPipelineService(
        storage=storage, channel_lister=channel_lister,
        download_dir=vod_download_dir, max_concurrent=1, timeout_seconds=0.0,
    )
    channel_lister.add_vod("casepayt", "100")
    profile = svc1.create_profile("casepayt")
    svc1.sync_vods(profile["id"])
    vod = svc1.list_vods(profile_id=profile["id"])[0]
    # Simulate a download interrupted by a server restart: set DOWNLOADING
    # and leave a .part file, with no live worker.
    vod["status"] = VodStatus.DOWNLOADING.value
    vod["progress"] = {"percent": 50.0, "downloaded_bytes": 100, "total_bytes": 200, "speed_bytes_per_second": 1.0, "eta_seconds": 1.0}
    svc1.storage.save_vod(vod)
    vod_dir = svc1._vod_dir(vod["id"])
    (vod_dir / ".dl_partial.part").write_bytes(b"partial")
    # "Restart": build a new service over the same data dir.
    svc2 = VodPipelineService(
        storage=storage, channel_lister=channel_lister,
        download_dir=vod_download_dir, max_concurrent=1, timeout_seconds=0.0,
    )
    recovered = svc2.get_vod(vod["id"])
    assert recovered["status"] == VodStatus.FAILED.value
    assert "interrupted" in (recovered["error"] or "").lower()
    # Partials cleaned.
    assert not (vod_dir / ".dl_partial.part").exists()


def test_ffprobe_rejects_no_video_stream(tmp_path, make_real_mp4):
    # Audio-only file -> FFprobe should reject it.
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    audio_only = tmp_path / "audio_only.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(audio_only)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    with pytest.raises(FFprobeError):
        ffprobe_inspect(audio_only)


def test_ffprobe_rejects_no_audio_stream(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    video_only = tmp_path / "video_only.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10", "-c:v", "libx264", "-an", str(video_only)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    with pytest.raises(FFprobeError):
        ffprobe_inspect(video_only)


def test_ffprobe_accepts_real_mp4(tmp_path, make_real_mp4):
    p = tmp_path / "real.mp4"
    make_real_mp4(p)
    info = ffprobe_inspect(p)
    assert info["width"] and info["height"]
    assert info["video_codec"]
    assert info["audio_codec"]
    assert info["duration_seconds"] and info["duration_seconds"] > 0


def test_file_endpoint_only_for_ready(vod_service, vod_with_profile):
    # Not READY -> no file path.
    assert vod_service.ready_file_path(vod_with_profile["id"]) is None


def test_delete_vod_removes_record_and_file(vod_service, vod_with_profile, make_real_mp4):
    vod = vod_with_profile
    vod["status"] = VodStatus.READY.value
    vod["download"]["file_name"] = "source.mp4"
    vod_service.storage.save_vod(vod)
    vod_dir = vod_service._vod_dir(vod["id"])
    src = vod_dir / "source.mp4"
    make_real_mp4(src)
    assert src.is_file()
    assert vod_service.delete_vod(vod["id"]) is True
    assert not vod_dir.exists()


def test_delete_vod_path_traversal_blocked(vod_service):
    from vod_pipeline import VodStorageError
    with pytest.raises((VodStorageError, Exception)):
        vod_service.delete_vod("..%2f..%2fetc")


def test_worker_log_excerpt_is_bounded_and_scrubbed(vod_service, vod_with_profile):
    vod_id = vod_with_profile["id"]
    log = vod_service.storage.vod_worker_log_path(vod_id)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("C:\\secret\\path info\nok line\n", encoding="utf-8")
    excerpt = vod_service.worker_log_excerpt(vod_id)
    assert excerpt is not None
    assert "C:\\secret" not in excerpt
    assert "ok line" in excerpt


def test_concurrent_progress_hook_writes_do_not_crash(tmp_path):
    """Multiple threads calling _atomic_write_json simultaneously must not
    race on the same tmp file (regression test for the WinError 2 bug).
    """
    import threading
    from vod_pipeline.downloader_worker import _atomic_write_json, _build_progress_hook, _ProgressThrottle

    meta_path = tmp_path / "metadata.json"
    throttle = _ProgressThrottle(interval=0.0)  # always write
    hook = _build_progress_hook(meta_path, throttle)
    errors: list[Exception] = []

    def writer():
        try:
            for i in range(50):
                hook({"status": "downloading", "downloaded_bytes": i * 100, "total_bytes": 5000, "speed": 1000.0, "eta": 5})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"concurrent writes failed: {errors}"
    # Final file is valid JSON.
    import json
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert data["status"] == "DOWNLOADING"


def test_thumbnail_timestamp_extraction():
    """VOD sort dates are parsed from the Twitch thumbnail URL when yt-dlp
    flat-playlist provides no timestamp/upload_date for VODs."""
    from vod_pipeline.service import _parse_thumbnail_timestamp
    thumb = "https://static-cdn.jtvnw.net/cf_vods/d3fi1amfgojobc/5f197dca05113246b469_casepayt_317894458983_1785083053//thumb/thumb0-320x180.jpg"
    result = _parse_thumbnail_timestamp(thumb)
    assert result is not None
    assert result.startswith("2026-")
    # No timestamp pattern -> None.
    assert _parse_thumbnail_timestamp("https://example.com/nope.jpg") is None
    assert _parse_thumbnail_timestamp("") is None


def test_real_worker_subprocess_downloads_local_file(vod_service, vod_with_profile, tmp_path, ffmpeg_available):
    """End-to-end: spawn the REAL downloader_worker (no mock) against a
    local HTTP-served MP4 and verify it reaches READY.

    This catches subprocess-invocation bugs (wrong argv, bad module path)
    that the mocked tests cannot detect. Uses a local HTTP server so it
    does not depend on network access.
    """
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe needed")
    import http.server
    import socketserver
    import threading
    ffmpeg = shutil.which("ffmpeg")
    # Create a tiny real MP4.
    src_dir = tmp_path / "serve"
    src_dir.mkdir()
    src = src_dir / "sample.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )
    # Serve it via a local HTTP server on a free port.
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(src_dir), **kw)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{port}/sample.mp4"
        # Point the VOD at the local HTTP URL.
        vod = vod_service.get_vod(vod_with_profile["id"])
        vod["source_url"] = url
        vod_service.storage.save_vod(vod)
        # Start the real (unpatched) worker.
        vod_service.start_download(vod_with_profile["id"])
        final = _wait_for_status(
            vod_service, vod_with_profile["id"],
            {VodStatus.READY.value, VodStatus.FAILED.value},
            timeout=60.0,
        )
        assert final is not None
        assert final["status"] == VodStatus.READY.value, f"worker failed: {final.get('error')}\nlog: {vod_service.worker_log_excerpt(vod_with_profile['id'])}"
        assert final["download"]["file_name"] == "source.mp4"
        assert final["download"]["file_size_bytes"] > 0
        assert final["download"]["video_codec"]
    finally:
        httpd.shutdown()
        httpd.server_close()
