from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ttvturbo.app_factory import create_app
from ttvturbo.editing import (
    EditConflictError,
    EditDatabase,
    EditProjectService,
    EditValidationError,
)
from ttvturbo.settings import Settings


@pytest.fixture()
def edit_service(tmp_path: Path) -> EditProjectService:
    return EditProjectService(EditDatabase(tmp_path / "editing" / "edit.sqlite3"))


def _project(service: EditProjectService):
    return service.create_project(
        name="Drake reaction",
        sources=[{"media_item_id": "media-1", "sha256": "a" * 64}],
    )


def _main(project: dict) -> dict:
    return next(b for b in project["branches"] if b["name"] == "main")


def test_migrations_are_idempotent(tmp_path: Path):
    path = tmp_path / "edit.sqlite3"
    EditDatabase(path)
    EditDatabase(path)
    service = EditProjectService(EditDatabase(path))
    assert service.list_projects() == []


def test_project_starts_with_desktop_and_mobile_sequences(edit_service: EditProjectService):
    project = _project(edit_service)
    assert {s["format_profile"] for s in project["sequences"]} == {
        "DESKTOP_16_9", "MOBILE_9_16"
    }
    assert project["branches"][0]["name"] == "main"
    assert project["checkout_commit_id"] == project["branches"][0]["head_commit_id"]


def test_atomic_commit_and_deterministic_reconstruction(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    commit = edit_service.create_commit(
        project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"],
        message="Add gameplay",
        operations=[
            {"type":"ADD_TRACK","sequence_id":seq["id"],"payload":{"track":{"id":"gameplay","type":"GAMEPLAY","name":"Gameplay"}}},
            {"type":"ADD_CLIP","sequence_id":seq["id"],"payload":{"track_id":"gameplay","clip":{"id":"clip-1","source_media_item_id":"media-1","source_start_us":1_000_000,"source_end_us":6_000_000,"timeline_start_us":0}}},
            {"type":"SET_TRANSFORM","sequence_id":seq["id"],"payload":{"track_id":"gameplay","clip_id":"clip-1","value":{"x":0.1,"y":0.2,"scale_x":1.2,"scale_y":1.2,"rotation":0}}},
        ],
    )
    state_a = edit_service.reconstruct_state(project["id"], commit["id"])
    state_b = edit_service.reconstruct_state(project["id"], commit["id"])
    assert state_a == state_b
    assert state_a["sequences"][seq["id"]]["tracks"]["gameplay"]["clips"]["clip-1"]["transform"]["x"] == 0.1
    stored = edit_service.get_commit(project["id"], commit["id"])
    assert len(stored["operations"]) == 3
    assert all("up_payload" in op and "down_payload" in op for op in stored["operations"])


def test_failed_multi_operation_commit_rolls_back(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    with pytest.raises(EditValidationError):
        edit_service.create_commit(
            project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="bad",
            operations=[
                {"type":"ADD_TRACK","sequence_id":seq["id"],"payload":{"track":{"id":"t","type":"VIDEO","name":"Video"}}},
                {"type":"ADD_TRACK","sequence_id":seq["id"],"payload":{"track":{"id":"t","type":"VIDEO","name":"Duplicate"}}},
            ],
        )
    fresh = edit_service.get_project(project["id"])
    assert fresh["branches"][0]["head_commit_id"] == main["head_commit_id"]
    state = edit_service.reconstruct_state(project["id"], main["head_commit_id"])
    assert "t" not in state["sequences"][seq["id"]]["tracks"]


def test_optimistic_head_conflict(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    first = edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="layout", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"A"}}])
    with pytest.raises(EditConflictError):
        edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="stale", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"B"}}])
    assert first["id"] == edit_service.list_branches(project["id"])[0]["head_commit_id"]


def test_detached_checkout_requires_branch_for_further_editing(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    commit = edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="one", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"A"}}])
    edit_service.checkout_commit(project["id"], main["head_commit_id"])
    with pytest.raises(EditConflictError):
        edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=commit["id"], message="detached", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"B"}}])
    branch = edit_service.create_branch(project["id"], name="from-old")
    created = edit_service.create_commit(project["id"], branch_id=branch["id"], expected_head_commit_id=branch["head_commit_id"], message="alternative", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"Alternative"}}])
    assert created["parent_ids"] == [main["head_commit_id"]]


def test_three_way_merge_of_independent_sequence_changes(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project)
    desktop, mobile = project["sequences"]
    branch = edit_service.create_branch(project["id"], name="mobile-alt", from_commit_id=main["head_commit_id"])
    main_commit = edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="desktop layout", operations=[{"type":"SET_LAYOUT","sequence_id":desktop["id"],"payload":{"layout":"desktop"}}])
    edit_service.create_commit(project["id"], branch_id=branch["id"], expected_head_commit_id=branch["head_commit_id"], message="mobile format", operations=[{"type":"SET_SEQUENCE_FORMAT","sequence_id":mobile["id"],"payload":{"width":720,"height":1280}}])
    preview = edit_service.preview_merge(project["id"], source_branch_id=branch["id"], target_branch_id=main["id"])
    assert preview["conflicts"] == []
    merged = edit_service.finalize_merge(project["id"], preview["id"])
    commit = edit_service.get_commit(project["id"], merged["merge_commit_id"], include_state=True)
    assert len(commit["parent_ids"]) == 2
    assert commit["state"]["sequences"][desktop["id"]]["layout"] == "desktop"
    assert commit["state"]["sequences"][mobile["id"]]["width"] == 720


def test_merge_conflict_and_manual_resolution(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    alt = edit_service.create_branch(project["id"], name="alt", from_commit_id=main["head_commit_id"])
    edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="ours", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"ours"}}])
    edit_service.create_commit(project["id"], branch_id=alt["id"], expected_head_commit_id=alt["head_commit_id"], message="theirs", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"theirs"}}])
    preview = edit_service.preview_merge(project["id"], source_branch_id=alt["id"], target_branch_id=main["id"])
    assert len(preview["conflicts"]) == 1
    conflict = preview["conflicts"][0]
    merged = edit_service.finalize_merge(project["id"], preview["id"], resolutions=[{"conflict_id":conflict["id"],"resolution":"MANUAL","value":"combined"}])
    state = edit_service.reconstruct_state(project["id"], merged["merge_commit_id"])
    assert state["sequences"][seq["id"]]["layout"] == "combined"


def test_revert_preserves_later_unrelated_changes(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); desktop, mobile = project["sequences"]
    first = edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="desktop", operations=[{"type":"SET_LAYOUT","sequence_id":desktop["id"],"payload":{"layout":"A"}}])
    second = edit_service.create_commit(project["id"], branch_id=main["id"], expected_head_commit_id=first["id"], message="mobile", operations=[{"type":"SET_LAYOUT","sequence_id":mobile["id"],"payload":{"layout":"B"}}])
    reverted = edit_service.revert_commits(project["id"], branch_id=main["id"], expected_head_commit_id=second["id"], commit_ids=[first["id"]])
    state = edit_service.reconstruct_state(project["id"], reverted["id"])
    assert state["sequences"][desktop["id"]]["layout"] is None
    assert state["sequences"][mobile["id"]]["layout"] == "B"


def test_derived_custom_sequence_is_independent(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); desktop = project["sequences"][0]
    result = edit_service.create_sequence(
        project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"],
        sequence={"name":"Square","width":1440,"height":1440,"fps_numerator":30,"fps_denominator":1,"format_profile":"CUSTOM"},
        derive_from_sequence_id=desktop["id"],
    )
    assert result["sequence"]["id"] != desktop["id"]
    assert result["sequence"]["width"] == 1440
    assert len(edit_service.list_sequences(project["id"])) == 3


class _FakeLibrary:
    def __init__(self, path: Path): self.path = path
    def item_file_path(self, item_id: str): return self.path


def test_render_projection_is_commit_pinned_and_detects_source_change(tmp_path: Path):
    source = tmp_path / "source.mp4"; source.write_bytes(b"original")
    sha = hashlib.sha256(b"original").hexdigest()
    service = EditProjectService(EditDatabase(tmp_path / "e.sqlite3"), library_service=_FakeLibrary(source))
    project = service.create_project(name="P", sources=[{"media_item_id":"m","sha256":sha}])
    projection = service.render_projection(project["id"], sequence_id=project["sequences"][0]["id"])
    assert projection["commit_id"] == project["checkout_commit_id"]
    assert projection["projection_hash"]
    source.write_bytes(b"changed")
    with pytest.raises(EditConflictError):
        service.render_projection(project["id"], sequence_id=project["sequences"][0]["id"])


def test_history_graph_contains_branch_heads_and_merge_edges(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project)
    edit_service.create_branch(project["id"], name="alt")
    graph = edit_service.history_graph(project["id"])
    assert len(graph["branches"]) == 2
    assert graph["nodes"]
    assert graph["total"] == len(graph["nodes"])


def test_api_end_to_end_uses_immutable_library_source(tmp_path: Path):
    settings = Settings(data_root=tmp_path / "data")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        library = app.state.container.library_service
        item = library.create_upload_item("source.mp4", title="Source")
        library.storage.write_item_file(item["id"], "source.mp4", b"video-bytes")
        created = client.post("/api/edit-projects", json={"name":"API Project","sources":[{"media_item_id":item["id"]}]})
        assert created.status_code == 201, created.text
        project = created.json(); main = project["branches"][0]; sequence = project["sequences"][0]
        commit = client.post(f"/api/edit-projects/{project['id']}/commits", json={
            "branch_id":main["id"], "expected_head_commit_id":main["head_commit_id"], "message":"layout",
            "operations":[{"type":"SET_LAYOUT","sequence_id":sequence["id"],"payload":{"layout":"gameplay_full_facecam_overlay"}}],
        })
        assert commit.status_code == 201, commit.text
        graph = client.get(f"/api/edit-projects/{project['id']}/history-graph")
        assert graph.status_code == 200
        assert graph.json()["total"] == 2
        projection = client.post(f"/api/edit-projects/{project['id']}/render-projections", json={"sequence_id":sequence["id"],"commit_id":commit.json()["id"]})
        assert projection.status_code == 201, projection.text
        assert projection.json()["state_hash"] == commit.json()["state_hash"]

def test_sequence_checkout_changes_active_sequence_without_commit(edit_service: EditProjectService):
    project = _project(edit_service)
    before = edit_service.history_graph(project["id"])["total"]
    target = project["sequences"][1]["id"]
    updated = edit_service.checkout_sequence(project["id"], target)
    assert updated["active_sequence_id"] == target
    assert edit_service.history_graph(project["id"])["total"] == before


def test_source_integrity_marks_changed_source_read_only(tmp_path: Path):
    source = tmp_path / "source.mp4"; source.write_bytes(b"original")
    sha = hashlib.sha256(b"original").hexdigest()
    service = EditProjectService(EditDatabase(tmp_path / "edit.sqlite3"), library_service=_FakeLibrary(source))
    project = service.create_project(name="P", sources=[{"media_item_id":"m","sha256":sha}])
    assert service.verify_sources(project["id"])["read_only"] is False
    source.write_bytes(b"changed")
    result = service.verify_sources(project["id"])
    assert result["read_only"] is True
    assert result["sources"][0]["status"] == "CHANGED"


def test_snapshot_replay_after_many_commits(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    head = main["head_commit_id"]
    for index in range(27):
        commit = edit_service.create_commit(
            project["id"], branch_id=main["id"], expected_head_commit_id=head,
            message=f"layout {index}",
            operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":{"revision":index}}}],
        )
        head = commit["id"]
    state = edit_service.reconstruct_state(project["id"], head)
    assert state["sequences"][seq["id"]]["layout"] == {"revision": 26}

def test_clip_cannot_reference_media_outside_project(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    with pytest.raises(EditValidationError):
        edit_service.create_commit(
            project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"], message="bad source",
            operations=[
                {"type":"ADD_TRACK","sequence_id":seq["id"],"payload":{"track":{"id":"video","type":"VIDEO","name":"Video"}}},
                {"type":"ADD_CLIP","sequence_id":seq["id"],"payload":{"track_id":"video","clip":{"id":"clip","source_media_item_id":"other","source_start_us":0,"source_end_us":1_000_000,"timeline_start_us":0}}},
            ],
        )


def test_duplicate_project_sources_rejected(edit_service: EditProjectService):
    with pytest.raises(EditValidationError):
        edit_service.create_project(name="P", sources=[
            {"media_item_id":"same","sha256":"a"*64},
            {"media_item_id":"same","sha256":"a"*64},
        ])

def test_commit_compare_and_child_navigation(edit_service: EditProjectService):
    project = _project(edit_service); main = _main(project); seq = project["sequences"][0]
    commit = edit_service.create_commit(
        project["id"], branch_id=main["id"], expected_head_commit_id=main["head_commit_id"],
        message="layout", operations=[{"type":"SET_LAYOUT","sequence_id":seq["id"],"payload":{"layout":"mobile"}}],
    )
    root = edit_service.get_commit(project["id"], main["head_commit_id"])
    assert commit["id"] in root["child_ids"]
    comparison = edit_service.compare_commits(project["id"], main["head_commit_id"], commit["id"])
    assert comparison["change_count"] == 1
    assert comparison["changes"][0]["path"].endswith(".layout")
