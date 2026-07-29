"""Tests for the natural-language editor command parser.

These tests do not run the real worker subprocess (which needs torch /
transformers / a GPU). They exercise the service orchestration with a
fake worker that writes the result file directly, plus the intent-JSON
extraction helper in the worker module.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ttvturbo.media_processing import (
    EditorCommandService,
    EditorCommandUnavailableError,
    EditorCommandValidationError,
    EditorCommandWorkerError,
)
from ttvturbo.media_processing.conversation_mining_worker import _parse_editor_intent_json, _build_editor_user_prompt
from ttvturbo.settings import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def editor_settings(vod_data_dir: Path):
    s = Settings(data_root=vod_data_dir)
    s.conversation_mining_model_id = "fake-model/test"
    s.conversation_mining_device = "cpu"
    return s


@pytest.fixture()
def gpu_lock(vod_data_dir: Path):
    from ttvturbo.media_processing import GpuLock
    return GpuLock(vod_data_dir)


@pytest.fixture()
def editor_service(gpu_lock, editor_settings):
    svc = EditorCommandService(
        gpu_lock=gpu_lock,
        settings=editor_settings,
        worker_python="python",
        timeout_seconds=5.0,
    )
    # The test environment has no transformers/torch; stub the checks so the
    # service reports available for the orchestration tests.
    svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
    svc._check_cuda_available = lambda: True  # noqa: SLF001
    svc._check_worker_module = lambda: True  # noqa: SLF001
    return svc


class _FakeProc:
    """Minimal Popen stand-in: poll() returns the configured exit code."""

    def __init__(self, rc: int = 0) -> None:
        self._rc = rc
        self._polls = 0

    def poll(self) -> int | None:
        self._polls += 1
        return self._rc


def _install_fake_worker(editor_service, *, result: dict) -> None:
    """Patch the service to skip the real subprocess and write *result*."""
    def fake_spawn(job_path: Path, log_path: Path) -> Any:
        import json as _json
        with open(job_path, "r", encoding="utf-8-sig") as fh:
            job = _json.load(fh)
        out = Path(job["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            _json.dump(result, fh)
        return _FakeProc(0)

    editor_service._spawn_worker = fake_spawn  # noqa: SLF001


# ---------------------------------------------------------------------------
# Intent-JSON extraction (pure helper in the worker module)
# ---------------------------------------------------------------------------


class TestIntentExtraction:
    def test_plain_json(self):
        assert _parse_editor_intent_json('{"action":"play"}') == {"action": "play"}

    def test_code_fence(self):
        raw = "```json\n{\"action\":\"split\"}\n```"
        assert _parse_editor_intent_json(raw) == {"action": "split"}

    def test_surrounding_prose(self):
        raw = 'Hier ist das JSON: {"action":"move","axis":"x","direction":"right"} fertig.'
        assert _parse_editor_intent_json(raw) == {"action": "move", "axis": "x", "direction": "right"}

    def test_missing_action_key_returns_none(self):
        assert _parse_editor_intent_json('{"foo":"bar"}') is None

    def test_empty_returns_none(self):
        assert _parse_editor_intent_json("") is None
        assert _parse_editor_intent_json("   ") is None


class TestEditorUserPrompt:
    def test_tracks_summary_is_included(self):
        ctx = {
            "sequence": {"width": 1920, "height": 1080},
            "playhead_seconds": 4.0,
            "selected_clip": None,
            "tracks": [
                {"id": "t1", "type": "VIDEO", "name": "Video", "clip_count": 2, "selected": False},
                {"id": "t2", "type": "AUDIO", "name": "Audio", "clip_count": 0, "selected": True},
            ],
        }
        prompt = _build_editor_user_prompt("entferne die leere audio spur", ctx)
        assert "tracks" in prompt
        assert '"clip_count": 0' in prompt
        assert '"type": "AUDIO"' in prompt
        assert "entferne die leere audio spur" in prompt

    def test_tracks_omitted_when_absent(self):
        prompt = _build_editor_user_prompt("Abspielen", {"sequence": {"width": 1920, "height": 1080}})
        assert '"tracks": null' in prompt


# ---------------------------------------------------------------------------
# Service orchestration
# ---------------------------------------------------------------------------


class TestEditorCommandService:
    def test_parse_applies_intent(self, editor_service):
        _install_fake_worker(editor_service, result={
            "ok": True,
            "intent": {"action": "move", "axis": "x", "direction": "right", "amount": 10, "unit": "percent"},
            "raw": '{"action":"move"}',
        })
        intent = editor_service.parse("Verschiebe den Clip 10% nach rechts", {"sequence": {"width": 1920, "height": 1080}})
        assert intent["action"] == "move"
        assert intent["axis"] == "x"

    def test_parse_empty_command_raises_validation(self, editor_service):
        with pytest.raises(EditorCommandValidationError):
            editor_service.parse("   ")

    def test_parse_unavailable_when_model_missing(self, gpu_lock, vod_data_dir):
        s = Settings(data_root=vod_data_dir)
        s.conversation_mining_model_id = ""
        svc = EditorCommandService(gpu_lock=gpu_lock, settings=s, worker_python="python")
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001
        with pytest.raises(EditorCommandUnavailableError):
            svc.parse("Zentriere den Clip")

    def test_parse_worker_failure_propagates(self, editor_service):
        _install_fake_worker(editor_service, result={"ok": False, "error": "model load failed"})
        with pytest.raises(EditorCommandWorkerError, match="model load failed"):
            editor_service.parse("Mach den Clip größer")

    def test_parse_worker_no_result_file(self, editor_service):
        # Fake worker that exits cleanly but writes nothing.
        editor_service._spawn_worker = lambda job_path, log_path: _FakeProc(0)  # noqa: SLF001
        with pytest.raises(EditorCommandWorkerError):
            editor_service.parse("Rotiere den Clip 15 Grad")

    def test_parse_timeout(self, editor_service):
        # Worker that never exits -> timeout path.
        class _HungProc:
            def poll(self):
                return None

        editor_service._spawn_worker = lambda job_path, log_path: _HungProc()  # noqa: SLF001
        editor_service._timeout_seconds = 0.2  # noqa: SLF001
        from ttvturbo.media_processing import EditorCommandTimeoutError
        with pytest.raises(EditorCommandTimeoutError):
            editor_service.parse("Zentriere den Clip")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(editor_settings):
    from ttvturbo.app_factory import create_app, ServiceOverrides
    from ttvturbo.media_processing import EditorCommandService
    # Build an available service with stubbed checks.
    gpu = __import__("ttvturbo.media_processing", fromlist=["GpuLock"]).GpuLock(editor_settings.data_root)
    svc = EditorCommandService(gpu_lock=gpu, settings=editor_settings, worker_python="python", timeout_seconds=5.0)
    svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
    svc._check_cuda_available = lambda: True  # noqa: SLF001
    svc._check_worker_module = lambda: True  # noqa: SLF001
    _install_fake_worker(svc, result={"ok": True, "intent": {"action": "play"}, "raw": "{}"})
    return create_app(settings=editor_settings, overrides=ServiceOverrides(editor_command_service=svc))


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


class TestEditorCommandApi:
    def test_status_endpoint(self, client):
        resp = client.get("/api/editor-command/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["model"] == "fake-model/test"

    def test_parse_endpoint_returns_intent(self, client):
        resp = client.post("/api/editor-command/parse", json={"command": "Abspielen", "context": {}})
        assert resp.status_code == 200
        assert resp.json()["intent"]["action"] == "play"

    def test_parse_endpoint_empty_command_returns_400(self, client):
        resp = client.post("/api/editor-command/parse", json={"command": "   ", "context": {}})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "editor_command_validation"

    def test_parse_endpoint_unavailable_returns_503(self, vod_data_dir, gpu_lock):
        from ttvturbo.app_factory import create_app, ServiceOverrides
        from ttvturbo.media_processing import EditorCommandService
        s = Settings(data_root=vod_data_dir)
        s.conversation_mining_model_id = ""
        svc = EditorCommandService(gpu_lock=gpu_lock, settings=s, worker_python="python")
        svc._check_dependencies = lambda: (True, None)  # noqa: SLF001
        svc._check_worker_module = lambda: True  # noqa: SLF001
        app = create_app(settings=s, overrides=ServiceOverrides(editor_command_service=svc))
        with TestClient(app) as c:
            resp = c.post("/api/editor-command/parse", json={"command": "Zentriere den Clip", "context": {}})
            assert resp.status_code == 503
            assert resp.json()["detail"]["code"] == "editor_command_unavailable"
