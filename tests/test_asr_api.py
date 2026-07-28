"""Tests for the ASR API endpoints.

Uses a stubbed benchmark service so no real models are loaded. The
default-preset store uses a real tmp directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asr_api import build_asr_router
from media_processing import AsrDefaultPresetStore


class _StubBenchmarkService:
    """In-memory stub that records calls and returns canned responses."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.running = False
        self.deleted: list[str] = []
        self.started: list[str] = []
        self.canceled: list[str] = []

    def is_running(self) -> bool:
        return self.running

    def list_benchmarks(self) -> list[dict[str, Any]]:
        return list(self.records.values())

    def get_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        if benchmark_id not in self.records:
            from media_processing.asr_benchmark import AsrBenchmarkNotFoundError
            raise AsrBenchmarkNotFoundError(f"benchmark not found: {benchmark_id}")
        return self.records[benchmark_id]

    def create_benchmark(self, source_type, source_id, preset_ids=None,
                         reference_text=None, hotwords=None,
                         candidate_ids=None, audio_variant=None) -> dict[str, Any]:
        # Mimic the real service's validation.
        from media_processing.asr_benchmark import AsrBenchmarkError
        from media_processing.asr_presets import get_preset, AsrPresetNotFoundError
        from media_processing.asr_models import CANDIDATE_MAP
        ids = candidate_ids if candidate_ids is not None else preset_ids
        if not ids:
            raise AsrBenchmarkError("at least one candidate is required")
        for cid in ids:
            if cid in CANDIDATE_MAP:
                continue
            try:
                get_preset(cid)
            except AsrPresetNotFoundError as exc:
                raise AsrBenchmarkError(str(exc)) from exc
        import uuid as _u
        bid = str(_u.uuid4())
        rec = {
            "id": bid, "source_type": source_type, "source_id": source_id,
            "selected_presets": ids, "candidate_ids": ids,
            "audio_variant": audio_variant,
            "reference_text": reference_text,
            "hotwords": hotwords, "status": "QUEUED", "runs": [],
        }
        self.records[bid] = rec
        return rec

    def start(self, benchmark_id: str) -> dict[str, Any]:
        self.started.append(benchmark_id)
        rec = self.get_benchmark(benchmark_id)
        rec["status"] = "RUNNING"
        return rec

    def cancel(self, benchmark_id: str) -> dict[str, Any]:
        self.canceled.append(benchmark_id)
        rec = self.get_benchmark(benchmark_id)
        rec["status"] = "CANCELED"
        return rec

    def delete(self, benchmark_id: str) -> bool:
        self.deleted.append(benchmark_id)
        self.records.pop(benchmark_id, None)
        return True

    def _runs_dir(self, benchmark_id: str) -> str:
        return f"/tmp/{benchmark_id}/runs"


@pytest.fixture()
def app(tmp_path: Path) -> FastAPI:
    svc = _StubBenchmarkService()
    store = AsrDefaultPresetStore(tmp_path)
    app = FastAPI()
    app.include_router(build_asr_router(benchmark_service=svc, default_store=store))
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_get_presets_returns_four(client: TestClient):
    resp = client.get("/api/asr/presets")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["presets"]}
    assert ids == {
        "legacy-current",
        "multilingual-large-v3-quality",
        "multilingual-large-v3-no-vad",
        "multilingual-large-v3-turbo",
    }


def test_status_returns_default(client: TestClient):
    resp = client.get("/api/asr/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["default_preset_id"] == "multilingual-large-v3-quality"


def test_set_default_refuses_no_vad(client: TestClient):
    resp = client.post("/api/asr/default", json={"preset_id": "multilingual-large-v3-no-vad"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "preset_invalid"


def test_set_default_unknown_preset(client: TestClient):
    resp = client.post("/api/asr/default", json={"preset_id": "unknown"})
    assert resp.status_code == 404


def test_set_default_persists(client: TestClient):
    resp = client.post("/api/asr/default", json={"preset_id": "multilingual-large-v3-turbo"})
    assert resp.status_code == 200
    assert resp.json()["preset_id"] == "multilingual-large-v3-turbo"
    # Subsequent status reflects it.
    assert client.get("/api/asr/status").json()["default_preset_id"] == "multilingual-large-v3-turbo"


def test_create_benchmark(client: TestClient):
    resp = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip",
        "source_id": "src-1",
        "preset_ids": ["legacy-current", "multilingual-large-v3-quality"],
        "reference_text": "ich ganken jetzt",
        "hotwords": "Flash Gank",
    })
    assert resp.status_code == 201
    rec = resp.json()
    assert rec["status"] == "QUEUED"
    assert rec["selected_presets"] == ["legacy-current", "multilingual-large-v3-quality"]


def test_create_benchmark_rejects_unknown_preset(client: TestClient):
    resp = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip",
        "source_id": "src-1",
        "preset_ids": ["unknown"],
    })
    # The service wraps the unknown-preset error as a benchmark conflict.
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "benchmark_conflict"


def test_list_benchmarks(client: TestClient):
    client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["legacy-current"],
    })
    resp = client.get("/api/asr/benchmarks")
    assert resp.status_code == 200
    assert len(resp.json()["benchmarks"]) >= 1


def test_get_benchmark(client: TestClient):
    rec = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["legacy-current"],
    }).json()
    resp = client.get(f"/api/asr/benchmarks/{rec['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == rec["id"]


def test_get_unknown_benchmark_404(client: TestClient):
    resp = client.get("/api/asr/benchmarks/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_start_benchmark(client: TestClient):
    rec = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["legacy-current"],
    }).json()
    resp = client.post(f"/api/asr/benchmarks/{rec['id']}/start")
    assert resp.status_code == 200
    assert resp.json()["status"] == "RUNNING"


def test_cancel_benchmark(client: TestClient):
    rec = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["legacy-current"],
    }).json()
    resp = client.post(f"/api/asr/benchmarks/{rec['id']}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELED"


def test_delete_benchmark(client: TestClient):
    rec = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["legacy-current"],
    }).json()
    resp = client.delete(f"/api/asr/benchmarks/{rec['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_select_default_from_benchmark_refuses_no_vad(client: TestClient):
    rec = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["multilingual-large-v3-no-vad"],
    }).json()
    resp = client.post(
        f"/api/asr/benchmarks/{rec['id']}/select-default",
        json={"preset_id": "multilingual-large-v3-no-vad"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "preset_not_eligible"


def test_select_default_from_benchmark_persists(client: TestClient):
    rec = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["multilingual-large-v3-turbo"],
    }).json()
    resp = client.post(
        f"/api/asr/benchmarks/{rec['id']}/select-default",
        json={"preset_id": "multilingual-large-v3-turbo"},
    )
    assert resp.status_code == 200
    assert resp.json()["preset_id"] == "multilingual-large-v3-turbo"


def test_get_run_not_found(client: TestClient):
    rec = client.post("/api/asr/benchmarks", json={
        "source_type": "twitch_clip", "source_id": "s1", "preset_ids": ["legacy-current"],
    }).json()
    resp = client.get(f"/api/asr/benchmarks/{rec['id']}/runs/legacy-current")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "run_not_found"
