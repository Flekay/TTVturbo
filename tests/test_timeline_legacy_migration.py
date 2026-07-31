from __future__ import annotations

from uuid import uuid4

from ttvturbo.editing import EditDatabase, EditProjectService
from ttvturbo.editing.operations import state_hash
from ttvturbo.storage_utils import now_iso


def test_legacy_overlapping_project_loads_and_is_persistently_migrated(tmp_path) -> None:
    service = EditProjectService(EditDatabase(tmp_path / "editing.sqlite3"))
    project = service.create_project(
        name="Legacy overlap",
        sources=[{"media_item_id": "media-1", "sha256": "a" * 64}],
    )
    branch = project["branches"][0]
    sequence_id = project["sequences"][0]["id"]

    with service.db.transaction() as conn:
        raw = service._reconstruct_raw_conn(conn, project["id"], branch["head_commit_id"])
        records = [
            service.engine.apply(raw, {
                "type": "ADD_TRACK",
                "sequence_id": sequence_id,
                "payload": {"track": {"id": "legacy-track", "type": "VIDEO", "name": "Video"}},
            }),
            service.engine.apply(raw, {
                "type": "ADD_CLIP",
                "sequence_id": sequence_id,
                "payload": {"track_id": "legacy-track", "clip": {
                    "id": "long", "source_media_item_id": "media-1",
                    "source_start_us": 0, "source_end_us": 14_001_000, "timeline_start_us": 0,
                }},
            }),
            service.engine.apply(raw, {
                "type": "ADD_CLIP",
                "sequence_id": sequence_id,
                "payload": {"track_id": "legacy-track", "clip": {
                    "id": "short", "source_media_item_id": "media-1",
                    "source_start_us": 0, "source_end_us": 5_000_000, "timeline_start_us": 0,
                }},
            }, validate_timeline_overlaps=False),
        ]
        legacy_commit_id = str(uuid4())
        ts = now_iso()
        conn.execute(
            "INSERT INTO edit_commits(id,project_id,author,message,state_hash,created_at) VALUES(?,?,?,?,?,?)",
            (legacy_commit_id, project["id"], None, "Legacy overlap", state_hash(raw), ts),
        )
        conn.execute(
            "INSERT INTO edit_commit_parents(commit_id,parent_commit_id,parent_order) VALUES(?,?,0)",
            (legacy_commit_id, branch["head_commit_id"]),
        )
        for index, record in enumerate(records):
            service._insert_operation(conn, legacy_commit_id, index, record)
        conn.execute(
            "UPDATE edit_branches SET head_commit_id=?,updated_at=? WHERE id=?",
            (legacy_commit_id, ts, branch["id"]),
        )

    loaded_project = service.get_project(project["id"])
    assert loaded_project["checkout_commit_id"] == legacy_commit_id
    loaded = service.reconstruct_state(project["id"], legacy_commit_id)
    sequence = loaded["sequences"][sequence_id]
    clip_tracks = {
        clip_id: track_id
        for track_id, track in sequence["tracks"].items()
        for clip_id in track.get("clips", {})
    }
    assert clip_tracks["long"] != clip_tracks["short"]
    assert all(track["type"] == "UNIVERSAL" for track in sequence["tracks"].values())

    source_result = service.add_source(
        project["id"],
        branch_id=branch["id"],
        expected_head_commit_id=legacy_commit_id,
        source={"media_item_id": "media-2", "sha256": "b" * 64},
        message="Attach new medium",
    )
    migration_commit = source_result["commit"]
    stored_migration = service.get_commit(project["id"], migration_commit["id"])
    assert stored_migration["operations"][0]["operation_type"] == "APPLY_STATE_PATCH"

    migrated_commit = service.create_commit(
        project["id"],
        branch_id=branch["id"],
        expected_head_commit_id=migration_commit["id"],
        message="Add after migration",
        operations=[{
            "type": "ADD_CLIP",
            "sequence_id": sequence_id,
            "payload": {
                "track_id": "legacy-track",
                "placement": "NEXT_AVAILABLE",
                "clip": {
                    "id": "third", "kind": "IMAGE", "source_media_item_id": "media-2",
                    "source_start_us": 0, "source_end_us": 2_000_000, "timeline_start_us": 0,
                },
            },
        }],
    )

    reconstructed = service.reconstruct_state(project["id"], migrated_commit["id"])
    track = reconstructed["sequences"][sequence_id]["tracks"]["legacy-track"]
    assert track["clips"]["third"]["timeline_start_us"] == 14_001_000
