"""Service layer for non-destructive, Git-like edit projects."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from ttvturbo.storage_utils import now_iso

from .database import EditDatabase
from .errors import EditConflictError, EditNotFoundError, EditStorageError, EditValidationError
from .merge import find_merge_base, set_path, state_diff, three_way_merge
from .operations import OperationEngine, canonical_json, empty_state, state_hash, validate_sequence
from .schemas import FormatProfile, SNAPSHOT_INTERVAL


def _uuid() -> str:
    return str(uuid4())


def _loads(value: Optional[str], default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class EditProjectService:
    """Persistent workspace, history, branch, merge and projection service."""

    def __init__(self, db: EditDatabase, *, library_service: Any = None) -> None:
        self.db = db
        self.library_service = library_service
        self.engine = OperationEngine()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _require_project(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM edit_projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise EditNotFoundError(f"edit project not found: {project_id}")
        return dict(row)

    @staticmethod
    def _require_branch(conn: sqlite3.Connection, project_id: str, branch_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM edit_branches WHERE id=? AND project_id=?", (branch_id, project_id)).fetchone()
        if row is None:
            raise EditNotFoundError(f"edit branch not found: {branch_id}")
        return dict(row)

    @staticmethod
    def _require_commit(conn: sqlite3.Connection, project_id: str, commit_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM edit_commits WHERE id=? AND project_id=?", (commit_id, project_id)).fetchone()
        if row is None:
            raise EditNotFoundError(f"edit commit not found: {commit_id}")
        return dict(row)

    @staticmethod
    def _parent_ids(conn: sqlite3.Connection, commit_id: str) -> list[str]:
        return [r[0] for r in conn.execute(
            "SELECT parent_commit_id FROM edit_commit_parents WHERE commit_id=? ORDER BY parent_order",
            (commit_id,),
        )]

    @staticmethod
    def _source_rows(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, media_item_id, asset_id, sha256, source_revision, created_at FROM edit_project_sources WHERE project_id=? ORDER BY created_at, id",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _operation_rows(conn: sqlite3.Connection, commit_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM edit_operations WHERE commit_id=? ORDER BY operation_order",
            (commit_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["up_payload"] = _loads(item.pop("up_payload_json"), {})
            item["down_payload"] = _loads(item.pop("down_payload_json"), {})
            result.append(item)
        return result

    def _reconstruct_conn(self, conn: sqlite3.Connection, project_id: str, commit_id: str) -> dict[str, Any]:
        target = self._require_commit(conn, project_id, commit_id)
        chain: list[str] = []
        cursor: Optional[str] = commit_id
        state: Optional[dict[str, Any]] = None
        while cursor is not None:
            snap = conn.execute("SELECT state_json, state_hash FROM edit_snapshots WHERE commit_id=?", (cursor,)).fetchone()
            if snap is not None:
                state = _loads(snap["state_json"], {})
                if state_hash(state) != snap["state_hash"]:
                    state = None
                else:
                    break
            chain.append(cursor)
            parents = self._parent_ids(conn, cursor)
            cursor = parents[0] if parents else None
        if state is None:
            state = empty_state(project_id, self._source_rows(conn, project_id))
        for cid in reversed(chain):
            for op in self._operation_rows(conn, cid):
                self.engine.replay(state, op)
        actual = state_hash(state)
        if actual != target["state_hash"]:
            raise EditStorageError(
                f"state hash mismatch for commit {commit_id}: expected {target['state_hash']}, got {actual}"
            )
        return state

    def reconstruct_state(self, project_id: str, commit_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            return self._reconstruct_conn(conn, project_id, commit_id)

    @staticmethod
    def _insert_snapshot(conn: sqlite3.Connection, commit_id: str, state: dict[str, Any]) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO edit_snapshots(commit_id,state_json,state_hash,created_at) VALUES(?,?,?,?)",
            (commit_id, canonical_json(state), state_hash(state), now_iso()),
        )

    def _maybe_snapshot(self, conn: sqlite3.Connection, project_id: str, commit_id: str, state: dict[str, Any], *, force: bool = False) -> None:
        count = int(conn.execute("SELECT COUNT(*) FROM edit_commits WHERE project_id=?", (project_id,)).fetchone()[0])
        if force or count == 1 or count % SNAPSHOT_INTERVAL == 0:
            self._insert_snapshot(conn, commit_id, state)

    def _insert_operation(self, conn: sqlite3.Connection, commit_id: str, order: int, record: dict[str, Any]) -> None:
        conn.execute(
            """INSERT INTO edit_operations(
                id,commit_id,sequence_id,operation_order,operation_type,entity_id,
                up_payload_json,down_payload_json,affected_path,start_us,end_us
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _uuid(), commit_id, record.get("sequence_id"), order,
                record["operation_type"], record.get("entity_id"),
                canonical_json(record["up_payload"]), canonical_json(record["down_payload"]),
                record.get("affected_path"), record.get("start_us"), record.get("end_us"),
            ),
        )

    def _sync_catalogs(self, conn: sqlite3.Connection, project_id: str, records: list[dict[str, Any]]) -> None:
        for record in records:
            if record["operation_type"] == "CREATE_SEQUENCE":
                seq = record["up_payload"]["sequence"]
                conn.execute(
                    """INSERT OR IGNORE INTO edit_sequences(
                        id,project_id,name,initial_width,initial_height,fps_numerator,fps_denominator,format_profile,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (seq["id"], project_id, seq["name"], seq["width"], seq["height"], seq["fps_numerator"], seq["fps_denominator"], seq["format_profile"], now_iso()),
                )
            elif record["operation_type"] == "ADD_SOURCE":
                source = record["up_payload"]["source"]
                conn.execute(
                    """INSERT OR IGNORE INTO edit_project_sources(
                        id,project_id,media_item_id,asset_id,sha256,source_revision,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        source["id"], project_id, source["media_item_id"], source.get("asset_id"),
                        source["sha256"], source.get("source_revision"), source["created_at"],
                    ),
                )

    def _create_commit_conn(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        branch_id: str,
        expected_head_commit_id: str,
        message: str,
        operations: list[dict[str, Any]],
        author: Optional[str] = None,
        second_parent_id: Optional[str] = None,
        force_snapshot: bool = False,
        allow_internal_operations: bool = False,
    ) -> dict[str, Any]:
        project = self._require_project(conn, project_id)
        branch = self._require_branch(conn, project_id, branch_id)
        if project.get("detached_commit_id") and project.get("active_branch_id") == branch_id:
            raise EditConflictError("cannot commit from detached checkout; create a branch first")
        if branch["head_commit_id"] != expected_head_commit_id:
            raise EditConflictError(
                f"branch head changed: expected {expected_head_commit_id}, current {branch['head_commit_id']}"
            )
        state = self._reconstruct_conn(conn, project_id, branch["head_commit_id"])
        records: list[dict[str, Any]] = []
        for operation in operations:
            records.append(self.engine.apply(state, operation, allow_internal=allow_internal_operations))
        if not records:
            raise EditValidationError("a commit must contain at least one operation")
        commit_id = _uuid(); created_at = now_iso(); digest = state_hash(state)
        conn.execute(
            "INSERT INTO edit_commits(id,project_id,author,message,state_hash,created_at) VALUES(?,?,?,?,?,?)",
            (commit_id, project_id, author, message.strip() or "Edit", digest, created_at),
        )
        conn.execute(
            "INSERT INTO edit_commit_parents(commit_id,parent_commit_id,parent_order) VALUES(?,?,0)",
            (commit_id, branch["head_commit_id"]),
        )
        if second_parent_id is not None:
            self._require_commit(conn, project_id, second_parent_id)
            conn.execute(
                "INSERT INTO edit_commit_parents(commit_id,parent_commit_id,parent_order) VALUES(?,?,1)",
                (commit_id, second_parent_id),
            )
        for index, record in enumerate(records):
            self._insert_operation(conn, commit_id, index, record)
        self._sync_catalogs(conn, project_id, records)
        conn.execute("UPDATE edit_branches SET head_commit_id=?,updated_at=? WHERE id=?", (commit_id, created_at, branch_id))
        active_sequence_id = project.get("active_sequence_id")
        if active_sequence_id not in state["sequences"]:
            active_sequence_id = next(iter(state["sequences"]), None)
        conn.execute("UPDATE edit_projects SET active_sequence_id=?,updated_at=? WHERE id=?", (active_sequence_id, created_at, project_id))
        self._maybe_snapshot(conn, project_id, commit_id, state, force=force_snapshot or second_parent_id is not None)
        return self._commit_payload(conn, commit_id, include_operations=True)

    @staticmethod
    def _commit_payload(conn: sqlite3.Connection, commit_id: str, *, include_operations: bool = False) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM edit_commits WHERE id=?", (commit_id,)).fetchone()
        if row is None:
            raise EditNotFoundError(f"commit not found: {commit_id}")
        payload = dict(row)
        payload["parent_ids"] = [r[0] for r in conn.execute(
            "SELECT parent_commit_id FROM edit_commit_parents WHERE commit_id=? ORDER BY parent_order", (commit_id,)
        )]
        payload["child_ids"] = [r[0] for r in conn.execute(
            "SELECT commit_id FROM edit_commit_parents WHERE parent_commit_id=? ORDER BY commit_id", (commit_id,)
        )]
        if include_operations:
            ops = []
            for r in conn.execute("SELECT * FROM edit_operations WHERE commit_id=? ORDER BY operation_order", (commit_id,)):
                op = dict(r); op["up_payload"] = _loads(op.pop("up_payload_json"), {}); op["down_payload"] = _loads(op.pop("down_payload_json"), {})
                ops.append(op)
            payload["operations"] = ops
        return payload


    def _library_path(self, media_item_id: str, asset_id: Optional[str] = None) -> Path:
        if self.library_service is None:
            raise EditValidationError("no library service is configured")
        if asset_id and hasattr(self.library_service, "asset_file_path"):
            return Path(self.library_service.asset_file_path(media_item_id, asset_id))
        return Path(self.library_service.item_file_path(media_item_id))

    def _resolve_source(self, source: dict[str, Any]) -> dict[str, Any]:
        media_item_id = str(source.get("media_item_id") or "").strip()
        if not media_item_id:
            raise EditValidationError("source media_item_id is required")
        asset_id = source.get("asset_id")
        supplied_hash = str(source.get("sha256") or "").lower()
        if self.library_service is not None:
            try:
                if hasattr(self.library_service, "get_item"):
                    item = self.library_service.get_item(media_item_id)
                    if item.get("lifecycle", "PERSISTENT") != "PERSISTENT":
                        raise EditValidationError(
                            "temporary media must be promoted before it can be used in an edit project"
                        )
                path = self._library_path(media_item_id, asset_id)
            except EditValidationError:
                raise
            except Exception as exc:
                raise EditValidationError(f"source media item is unavailable: {media_item_id}") from exc
            actual_hash = _sha256_file(path)
        elif supplied_hash:
            actual_hash = supplied_hash
        else:
            raise EditValidationError("source sha256 is required when no library service is configured")
        if supplied_hash and supplied_hash != actual_hash:
            raise EditConflictError(f"source hash mismatch for media item {media_item_id}")
        return {
            "id": _uuid(), "media_item_id": media_item_id, "asset_id": asset_id,
            "sha256": actual_hash, "source_revision": source.get("source_revision"), "created_at": now_iso(),
        }

    # ------------------------------------------------------------------ projects
    def create_project(
        self,
        *,
        name: str,
        sources: list[dict[str, Any]],
        sequences: Optional[list[dict[str, Any]]] = None,
        author: Optional[str] = None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise EditValidationError("project name must not be empty")
        resolved_sources = [self._resolve_source(s) for s in sources]
        source_keys = [(src["media_item_id"], src.get("asset_id")) for src in resolved_sources]
        if len(source_keys) != len(set(source_keys)):
            raise EditValidationError("duplicate project sources are not allowed")
        if sequences is None:
            sequences = [
                {"id": _uuid(), "name": "Desktop", "width": 1920, "height": 1080, "fps_numerator": 60, "fps_denominator": 1, "format_profile": FormatProfile.DESKTOP_16_9.value},
                {"id": _uuid(), "name": "Mobile", "width": 1080, "height": 1920, "fps_numerator": 60, "fps_denominator": 1, "format_profile": FormatProfile.MOBILE_9_16.value},
            ]
        checked = [validate_sequence({**s, "id": s.get("id") or _uuid()}) for s in sequences]
        if not checked:
            raise EditValidationError("at least one sequence is required")
        project_id = _uuid(); commit_id = _uuid(); branch_id = _uuid(); ts = now_iso()
        state = empty_state(project_id, resolved_sources); records = []
        for seq in checked:
            records.append(self.engine.apply(state, {"type":"CREATE_SEQUENCE", "payload":{"sequence":seq}}))
        digest = state_hash(state)
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO edit_projects(id,name,active_branch_id,active_sequence_id,detached_commit_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (project_id,name.strip(),None,checked[0]["id"],None,ts,ts))
            for source in resolved_sources:
                conn.execute("INSERT INTO edit_project_sources(id,project_id,media_item_id,asset_id,sha256,source_revision,created_at) VALUES(?,?,?,?,?,?,?)", (source["id"],project_id,source["media_item_id"],source.get("asset_id"),source["sha256"],source.get("source_revision"),source["created_at"]))
            conn.execute("INSERT INTO edit_commits(id,project_id,author,message,state_hash,created_at) VALUES(?,?,?,?,?,?)", (commit_id,project_id,author,"Initial project",digest,ts))
            for index, record in enumerate(records): self._insert_operation(conn, commit_id, index, record)
            self._sync_catalogs(conn, project_id, records)
            conn.execute("INSERT INTO edit_branches(id,project_id,name,head_commit_id,created_from_commit_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (branch_id,project_id,"main",commit_id,commit_id,ts,ts))
            conn.execute("UPDATE edit_projects SET active_branch_id=? WHERE id=?", (branch_id, project_id))
            self._insert_snapshot(conn, commit_id, state)
        return self.get_project(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM edit_projects ORDER BY updated_at DESC, id").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["branch_count"] = int(conn.execute("SELECT COUNT(*) FROM edit_branches WHERE project_id=?", (item["id"],)).fetchone()[0])
                item["sequence_count"] = int(conn.execute("SELECT COUNT(*) FROM edit_sequences WHERE project_id=?", (item["id"],)).fetchone()[0])
                result.append(item)
            return result

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            project = self._require_project(conn, project_id)
            project["sources"] = self._source_rows(conn, project_id)
            project["branches"] = [dict(r) for r in conn.execute("SELECT * FROM edit_branches WHERE project_id=? ORDER BY created_at,id", (project_id,))]
            checkout_commit = project.get("detached_commit_id")
            if not checkout_commit and project.get("active_branch_id"):
                branch = self._require_branch(conn, project_id, project["active_branch_id"])
                checkout_commit = branch["head_commit_id"]
            project["checkout_commit_id"] = checkout_commit
            if checkout_commit:
                state = self._reconstruct_conn(conn, project_id, checkout_commit)
                project["sequences"] = list(state["sequences"].values())
                project["state_hash"] = state_hash(state)
            else:
                project["sequences"] = []
            return project

    def delete_project(self, project_id: str) -> bool:
        with self.db.transaction() as conn:
            self._require_project(conn, project_id)
            # Manual cleanup in dependency order. Several tables reference
            # edit_commits with ON DELETE RESTRICT (commit parents, branch
            # heads, merge sessions, render artifacts), so the cascade from
            # edit_projects alone cannot delete a project that has commits.
            commit_ids = [r[0] for r in conn.execute("SELECT id FROM edit_commits WHERE project_id=?", (project_id,)).fetchall()]
            if commit_ids:
                placeholders = ",".join("?" * len(commit_ids))
                # merge_conflicts -> merge_sessions (cascade), but delete
                # sessions explicitly to clear RESTRICT refs to commits.
                conn.execute(f"DELETE FROM edit_merge_conflicts WHERE merge_id IN (SELECT id FROM edit_merge_sessions WHERE project_id=?)", (project_id,))
                conn.execute("DELETE FROM edit_merge_sessions WHERE project_id=?", (project_id,))
                conn.execute("DELETE FROM edit_render_artifacts WHERE project_id=?", (project_id,))
                conn.execute("DELETE FROM edit_branches WHERE project_id=?", (project_id,))
                conn.execute(f"DELETE FROM edit_commit_parents WHERE commit_id IN ({placeholders}) OR parent_commit_id IN ({placeholders})", (*commit_ids, *commit_ids))
                conn.execute(f"DELETE FROM edit_operations WHERE commit_id IN ({placeholders})", commit_ids)
                conn.execute(f"DELETE FROM edit_snapshots WHERE commit_id IN ({placeholders})", commit_ids)
                conn.execute("DELETE FROM edit_commits WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM edit_sequences WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM edit_project_sources WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM edit_projects WHERE id=?", (project_id,))
        return True

    # ------------------------------------------------------------------ commits
    def create_commit(self, project_id: str, *, branch_id: str, expected_head_commit_id: str, message: str, operations: list[dict[str, Any]], author: Optional[str] = None) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return self._create_commit_conn(conn, project_id=project_id, branch_id=branch_id, expected_head_commit_id=expected_head_commit_id, message=message, operations=operations, author=author)

    def list_commits(self, project_id: str, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500)); offset = max(0, int(offset))
        with self.db.read() as conn:
            self._require_project(conn, project_id)
            rows = conn.execute("SELECT id FROM edit_commits WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (project_id,limit,offset)).fetchall()
            commits = [self._commit_payload(conn, r[0]) for r in rows]
            total = int(conn.execute("SELECT COUNT(*) FROM edit_commits WHERE project_id=?", (project_id,)).fetchone()[0])
            return {"commits":commits,"total":total,"limit":limit,"offset":offset}

    def get_commit(self, project_id: str, commit_id: str, *, include_state: bool = False) -> dict[str, Any]:
        with self.db.read() as conn:
            self._require_commit(conn, project_id, commit_id)
            payload = self._commit_payload(conn, commit_id, include_operations=True)
            if include_state: payload["state"] = self._reconstruct_conn(conn, project_id, commit_id)
            return payload

    def checkout_commit(self, project_id: str, commit_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            project = self._require_project(conn, project_id); self._require_commit(conn, project_id, commit_id)
            state = self._reconstruct_conn(conn, project_id, commit_id)
            active_sequence_id = project.get("active_sequence_id")
            if active_sequence_id not in state["sequences"]:
                active_sequence_id = next(iter(state["sequences"]), None)
            conn.execute("UPDATE edit_projects SET detached_commit_id=?,active_sequence_id=?,updated_at=? WHERE id=?", (commit_id,active_sequence_id,now_iso(),project_id))
        return self.get_project(project_id)

    def revert_commits(self, project_id: str, *, branch_id: str, expected_head_commit_id: str, commit_ids: list[str], message: Optional[str] = None, author: Optional[str] = None) -> dict[str, Any]:
        if not commit_ids: raise EditValidationError("commit_ids must not be empty")
        with self.db.transaction() as conn:
            branch = self._require_branch(conn, project_id, branch_id)
            if branch["head_commit_id"] != expected_head_commit_id: raise EditConflictError("branch head changed")
            current = self._reconstruct_conn(conn, project_id, expected_head_commit_id)
            for target_id in commit_ids:
                self._require_commit(conn, project_id, target_id)
                parents = self._parent_ids(conn, target_id)
                if not parents: raise EditValidationError("the initial commit cannot be reverted")
                target = self._reconstruct_conn(conn, project_id, target_id)
                parent = self._reconstruct_conn(conn, project_id, parents[0])
                merged, conflicts = three_way_merge(target, current, parent)
                if conflicts:
                    raise EditConflictError(f"revert conflicts at: {', '.join(c['path'] for c in conflicts[:10])}")
                current = merged
            op = {"type":"APPLY_STATE_PATCH","payload":{"state":current}}
            # Internal operation: insert explicitly because public commit API rejects it.
            state_before = self._reconstruct_conn(conn, project_id, expected_head_commit_id)
            record = self.engine.apply(state_before, op, allow_internal=True)
            return self._create_internal_patch_commit(conn, project_id, branch_id, expected_head_commit_id, message or f"Revert {len(commit_ids)} commit(s)", record, current, author=author)

    def _create_internal_patch_commit(self, conn: sqlite3.Connection, project_id: str, branch_id: str, expected_head: str, message: str, record: dict[str, Any], final_state: dict[str, Any], *, author: Optional[str] = None, second_parent_id: Optional[str] = None) -> dict[str, Any]:
        branch = self._require_branch(conn, project_id, branch_id)
        if branch["head_commit_id"] != expected_head: raise EditConflictError("branch head changed")
        commit_id = _uuid(); ts = now_iso(); digest = state_hash(final_state)
        conn.execute("INSERT INTO edit_commits(id,project_id,author,message,state_hash,created_at) VALUES(?,?,?,?,?,?)", (commit_id,project_id,author,message,digest,ts))
        conn.execute("INSERT INTO edit_commit_parents(commit_id,parent_commit_id,parent_order) VALUES(?,?,0)", (commit_id,expected_head))
        if second_parent_id:
            conn.execute("INSERT INTO edit_commit_parents(commit_id,parent_commit_id,parent_order) VALUES(?,?,1)", (commit_id,second_parent_id))
        self._insert_operation(conn, commit_id, 0, record)
        conn.execute("UPDATE edit_branches SET head_commit_id=?,updated_at=? WHERE id=?", (commit_id,ts,branch_id))
        project = self._require_project(conn, project_id)
        active_sequence_id = project.get("active_sequence_id")
        if active_sequence_id not in final_state["sequences"]:
            active_sequence_id = next(iter(final_state["sequences"]), None)
        conn.execute("UPDATE edit_projects SET active_sequence_id=?,updated_at=? WHERE id=?", (active_sequence_id,ts,project_id))
        self._insert_snapshot(conn, commit_id, final_state)
        return self._commit_payload(conn, commit_id, include_operations=True)

    def add_source(
        self,
        project_id: str,
        *,
        branch_id: str,
        expected_head_commit_id: str,
        source: dict[str, Any],
        message: Optional[str] = None,
        author: Optional[str] = None,
    ) -> dict[str, Any]:
        """Attach an immutable persistent Library item to an existing project."""
        resolved = self._resolve_source(source)
        with self.db.transaction() as conn:
            existing = conn.execute(
                """SELECT id,media_item_id,asset_id,sha256,source_revision,created_at
                   FROM edit_project_sources
                   WHERE project_id=? AND media_item_id=?
                     AND ((asset_id=? ) OR (asset_id IS NULL AND ? IS NULL))
                   ORDER BY created_at,id LIMIT 1""",
                (project_id, resolved["media_item_id"], resolved.get("asset_id"), resolved.get("asset_id")),
            ).fetchone()
            if existing is not None:
                resolved = dict(existing)
            state = self._reconstruct_conn(conn, project_id, expected_head_commit_id)
            key = (resolved["media_item_id"], resolved.get("asset_id"))
            keys = {(str(item.get("media_item_id")), item.get("asset_id")) for item in state.get("sources", [])}
            if key in keys or (key[0], None) in keys:
                raise EditConflictError("project source already exists")
            commit = self._create_commit_conn(
                conn,
                project_id=project_id,
                branch_id=branch_id,
                expected_head_commit_id=expected_head_commit_id,
                message=message or "Add media source",
                operations=[{"type": "ADD_SOURCE", "payload": {"source": resolved}}],
                author=author,
                allow_internal_operations=True,
            )
            return {"source": resolved, "commit": commit}

    # ------------------------------------------------------------------ sequences
    def list_sequences(self, project_id: str, *, commit_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self.db.read() as conn:
            project = self._require_project(conn, project_id)
            cid = commit_id or project.get("detached_commit_id")
            if cid is None:
                cid = self._require_branch(conn, project_id, project["active_branch_id"])["head_commit_id"]
            state = self._reconstruct_conn(conn, project_id, cid)
            return list(state["sequences"].values())

    def create_sequence(self, project_id: str, *, branch_id: str, expected_head_commit_id: str, sequence: dict[str, Any], derive_from_sequence_id: Optional[str] = None, message: Optional[str] = None) -> dict[str, Any]:
        with self.db.transaction() as conn:
            state = self._reconstruct_conn(conn, project_id, expected_head_commit_id)
            if derive_from_sequence_id:
                try: seq = copy.deepcopy(state["sequences"][derive_from_sequence_id])
                except KeyError as exc: raise EditNotFoundError(f"sequence not found: {derive_from_sequence_id}") from exc
                seq.update(sequence); seq["id"] = sequence.get("id") or _uuid(); seq["name"] = sequence.get("name") or f"{seq['name']} Copy"
            else:
                seq = dict(sequence); seq.setdefault("id", _uuid())
            seq = validate_sequence(seq)
            commit = self._create_commit_conn(conn, project_id=project_id, branch_id=branch_id, expected_head_commit_id=expected_head_commit_id, message=message or f"Create sequence {seq['name']}", operations=[{"type":"CREATE_SEQUENCE","payload":{"sequence":seq}}])
            return {"sequence":seq,"commit":commit}

    def update_sequence(self, project_id: str, sequence_id: str, *, branch_id: str, expected_head_commit_id: str, updates: dict[str, Any], message: Optional[str] = None) -> dict[str, Any]:
        allowed = {k:v for k,v in updates.items() if k in {"width","height","fps_numerator","fps_denominator","format_profile","safe_area_enabled","safe_area_margin_top","safe_area_margin_right","safe_area_margin_bottom","safe_area_margin_left"}}
        if not allowed: raise EditValidationError("no supported sequence format fields supplied")
        with self.db.transaction() as conn:
            commit = self._create_commit_conn(conn, project_id=project_id, branch_id=branch_id, expected_head_commit_id=expected_head_commit_id, message=message or "Update sequence format", operations=[{"type":"SET_SEQUENCE_FORMAT","sequence_id":sequence_id,"payload":allowed}])
            state = self._reconstruct_conn(conn, project_id, commit["id"])
            return {"sequence":state["sequences"][sequence_id],"commit":commit}


    def checkout_sequence(self, project_id: str, sequence_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            project = self._require_project(conn, project_id)
            commit_id = project.get("detached_commit_id")
            if commit_id is None:
                commit_id = self._require_branch(conn, project_id, project["active_branch_id"])["head_commit_id"]
            state = self._reconstruct_conn(conn, project_id, commit_id)
            if sequence_id not in state["sequences"]:
                raise EditNotFoundError(f"sequence not found at current checkout: {sequence_id}")
            conn.execute("UPDATE edit_projects SET active_sequence_id=?,updated_at=? WHERE id=?", (sequence_id, now_iso(), project_id))
        return self.get_project(project_id)

    def verify_sources(self, project_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            self._require_project(conn, project_id)
            sources = self._source_rows(conn, project_id)
        results: list[dict[str, Any]] = []
        read_only = False
        for source in sources:
            status = "UNVERIFIED"
            actual_hash = None
            reason = None
            if self.library_service is not None:
                try:
                    path = self._library_path(source["media_item_id"], source.get("asset_id"))
                    actual_hash = _sha256_file(path)
                    if actual_hash == source["sha256"]:
                        status = "OK"
                    else:
                        status = "CHANGED"
                        reason = "source hash no longer matches the project reference"
                        read_only = True
                except Exception:
                    status = "MISSING"
                    reason = "source file is missing"
                    read_only = True
            results.append({
                "source_id": source["id"],
                "media_item_id": source["media_item_id"],
                "asset_id": source.get("asset_id"),
                "expected_sha256": source["sha256"],
                "actual_sha256": actual_hash,
                "status": status,
                "reason": reason,
            })
        return {"project_id": project_id, "read_only": read_only, "sources": results}

    # ------------------------------------------------------------------ branches
    def list_branches(self, project_id: str) -> list[dict[str, Any]]:
        with self.db.read() as conn:
            self._require_project(conn, project_id)
            return [dict(r) for r in conn.execute("SELECT * FROM edit_branches WHERE project_id=? ORDER BY created_at,id", (project_id,))]

    def create_branch(self, project_id: str, *, name: str, from_commit_id: Optional[str] = None) -> dict[str, Any]:
        if not name.strip(): raise EditValidationError("branch name must not be empty")
        with self.db.transaction() as conn:
            project = self._require_project(conn, project_id)
            if from_commit_id is None:
                from_commit_id = project.get("detached_commit_id") or self._require_branch(conn, project_id, project["active_branch_id"])["head_commit_id"]
            self._require_commit(conn, project_id, from_commit_id)
            bid = _uuid(); ts = now_iso()
            try:
                conn.execute("INSERT INTO edit_branches(id,project_id,name,head_commit_id,created_from_commit_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (bid,project_id,name.strip(),from_commit_id,from_commit_id,ts,ts))
            except sqlite3.IntegrityError as exc:
                raise EditConflictError(f"branch name already exists: {name}") from exc
            return dict(conn.execute("SELECT * FROM edit_branches WHERE id=?", (bid,)).fetchone())

    def rename_branch(self, project_id: str, branch_id: str, name: str) -> dict[str, Any]:
        if not name.strip(): raise EditValidationError("branch name must not be empty")
        with self.db.transaction() as conn:
            self._require_branch(conn, project_id, branch_id)
            try: conn.execute("UPDATE edit_branches SET name=?,updated_at=? WHERE id=?", (name.strip(),now_iso(),branch_id))
            except sqlite3.IntegrityError as exc: raise EditConflictError(f"branch name already exists: {name}") from exc
            return dict(conn.execute("SELECT * FROM edit_branches WHERE id=?", (branch_id,)).fetchone())

    def delete_branch(self, project_id: str, branch_id: str) -> bool:
        with self.db.transaction() as conn:
            project = self._require_project(conn, project_id); self._require_branch(conn, project_id, branch_id)
            count = int(conn.execute("SELECT COUNT(*) FROM edit_branches WHERE project_id=?", (project_id,)).fetchone()[0])
            if count <= 1: raise EditValidationError("cannot delete the last branch")
            if project.get("active_branch_id") == branch_id: raise EditConflictError("cannot delete the active branch")
            conn.execute("DELETE FROM edit_branches WHERE id=?", (branch_id,))
        return True

    def checkout_branch(self, project_id: str, branch_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            project = self._require_project(conn, project_id)
            branch = self._require_branch(conn, project_id, branch_id)
            state = self._reconstruct_conn(conn, project_id, branch["head_commit_id"])
            active_sequence_id = project.get("active_sequence_id")
            if active_sequence_id not in state["sequences"]:
                active_sequence_id = next(iter(state["sequences"]), None)
            conn.execute("UPDATE edit_projects SET active_branch_id=?,detached_commit_id=NULL,active_sequence_id=?,updated_at=? WHERE id=?", (branch_id,active_sequence_id,now_iso(),project_id))
        return self.get_project(project_id)

    def reset_branch(self, project_id: str, branch_id: str, *, expected_head_commit_id: str, target_commit_id: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed: raise EditValidationError("branch reset requires confirmed=true")
        with self.db.transaction() as conn:
            branch = self._require_branch(conn, project_id, branch_id); self._require_commit(conn, project_id, target_commit_id)
            if branch["head_commit_id"] != expected_head_commit_id: raise EditConflictError("branch head changed")
            conn.execute("UPDATE edit_branches SET head_commit_id=?,updated_at=? WHERE id=?", (target_commit_id,now_iso(),branch_id))
            project = self._require_project(conn, project_id)
            if project.get("active_branch_id") == branch_id:
                state = self._reconstruct_conn(conn, project_id, target_commit_id)
                active_sequence_id = project.get("active_sequence_id")
                if active_sequence_id not in state["sequences"]:
                    active_sequence_id = next(iter(state["sequences"]), None)
                conn.execute("UPDATE edit_projects SET detached_commit_id=NULL,active_sequence_id=?,updated_at=? WHERE id=?", (active_sequence_id,now_iso(),project_id))
            return dict(conn.execute("SELECT * FROM edit_branches WHERE id=?", (branch_id,)).fetchone())

    # ------------------------------------------------------------------ merge
    def preview_merge(self, project_id: str, *, source_branch_id: str, target_branch_id: str) -> dict[str, Any]:
        if source_branch_id == target_branch_id: raise EditValidationError("cannot merge a branch into itself")
        with self.db.transaction() as conn:
            source = self._require_branch(conn, project_id, source_branch_id); target = self._require_branch(conn, project_id, target_branch_id)
            base_id = find_merge_base(conn, target["head_commit_id"], source["head_commit_id"])
            base = self._reconstruct_conn(conn, project_id, base_id); ours = self._reconstruct_conn(conn, project_id, target["head_commit_id"]); theirs = self._reconstruct_conn(conn, project_id, source["head_commit_id"])
            merged, conflicts = three_way_merge(base, ours, theirs)
            mid = _uuid(); ts = now_iso(); status = "CONFLICTS" if conflicts else "READY"
            conn.execute("INSERT INTO edit_merge_sessions(id,project_id,source_branch_id,target_branch_id,merge_base_commit_id,ours_commit_id,theirs_commit_id,status,merged_state_json,merge_commit_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (mid,project_id,source_branch_id,target_branch_id,base_id,target["head_commit_id"],source["head_commit_id"],status,canonical_json(merged),None,ts,ts))
            for conflict in conflicts:
                conn.execute("INSERT INTO edit_merge_conflicts(id,merge_id,path,kind,base_payload_json,ours_payload_json,theirs_payload_json,resolution,resolved_payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (_uuid(),mid,conflict["path"],conflict["kind"],canonical_json(conflict["base"]),canonical_json(conflict["ours"]),canonical_json(conflict["theirs"]),None,None,ts,ts))
            return self._merge_payload(conn, mid)

    @staticmethod
    def _merge_payload(conn: sqlite3.Connection, merge_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM edit_merge_sessions WHERE id=?", (merge_id,)).fetchone()
        if row is None: raise EditNotFoundError(f"merge not found: {merge_id}")
        payload = dict(row); payload.pop("merged_state_json", None)
        conflicts = []
        for r in conn.execute("SELECT * FROM edit_merge_conflicts WHERE merge_id=? ORDER BY path", (merge_id,)):
            c = dict(r)
            for key in ("base_payload_json","ours_payload_json","theirs_payload_json","resolved_payload_json"):
                c[key.removesuffix("_json")] = _loads(c.pop(key), None)
            conflicts.append(c)
        payload["conflicts"] = conflicts
        payload["fast_forward"] = payload["merge_base_commit_id"] == payload["ours_commit_id"]
        payload["already_up_to_date"] = payload["merge_base_commit_id"] == payload["theirs_commit_id"]
        return payload

    def get_merge(self, project_id: str, merge_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = conn.execute("SELECT id FROM edit_merge_sessions WHERE id=? AND project_id=?", (merge_id,project_id)).fetchone()
            if row is None: raise EditNotFoundError(f"merge not found: {merge_id}")
            return self._merge_payload(conn, merge_id)

    def finalize_merge(self, project_id: str, merge_id: str, *, resolutions: Optional[list[dict[str, Any]]] = None, message: Optional[str] = None, author: Optional[str] = None) -> dict[str, Any]:
        resolution_map = {str(r.get("conflict_id")): r for r in (resolutions or [])}
        with self.db.transaction() as conn:
            session_row = conn.execute("SELECT * FROM edit_merge_sessions WHERE id=? AND project_id=?", (merge_id,project_id)).fetchone()
            if session_row is None: raise EditNotFoundError(f"merge not found: {merge_id}")
            session = dict(session_row)
            if session["status"] == "MERGED": return self._merge_payload(conn, merge_id)
            source = self._require_branch(conn, project_id, session["source_branch_id"]); target = self._require_branch(conn, project_id, session["target_branch_id"])
            if source["head_commit_id"] != session["theirs_commit_id"] or target["head_commit_id"] != session["ours_commit_id"]:
                raise EditConflictError("a branch head moved after merge preview")
            merged = _loads(session["merged_state_json"], {})
            conflict_rows = conn.execute("SELECT * FROM edit_merge_conflicts WHERE merge_id=? ORDER BY path", (merge_id,)).fetchall()
            for row in conflict_rows:
                c = dict(row); supplied = resolution_map.get(c["id"])
                resolution = (supplied or {}).get("resolution") or c.get("resolution")
                value = (supplied or {}).get("value") if supplied else _loads(c.get("resolved_payload_json"), None)
                if resolution not in {"OURS","THEIRS","MANUAL"}: raise EditConflictError(f"unresolved merge conflict: {c['path']}")
                delete_value = False
                if resolution == "OURS":
                    value = _loads(c["ours_payload_json"], None)
                    delete_value = c["kind"] == "DELETE_MODIFY"
                elif resolution == "THEIRS":
                    value = _loads(c["theirs_payload_json"], None)
                    delete_value = c["kind"] == "MODIFY_DELETE"
                else:
                    delete_value = bool((supplied or {}).get("delete", False))
                set_path(merged, c["path"], value, delete=delete_value)
                conn.execute("UPDATE edit_merge_conflicts SET resolution=?,resolved_payload_json=?,updated_at=? WHERE id=?", (resolution,canonical_json(value),now_iso(),c["id"]))
            if session["merge_base_commit_id"] == session["ours_commit_id"]:
                conn.execute("UPDATE edit_branches SET head_commit_id=?,updated_at=? WHERE id=?", (session["theirs_commit_id"],now_iso(),target["id"]))
                merge_commit_id = session["theirs_commit_id"]
            elif session["merge_base_commit_id"] == session["theirs_commit_id"]:
                merge_commit_id = session["ours_commit_id"]
            else:
                ours_state = self._reconstruct_conn(conn, project_id, session["ours_commit_id"])
                record = self.engine.apply(ours_state, {"type":"APPLY_STATE_PATCH","payload":{"state":merged}}, allow_internal=True)
                commit = self._create_internal_patch_commit(conn, project_id, target["id"], session["ours_commit_id"], message or f"Merge {source['name']} into {target['name']}", record, merged, author=author, second_parent_id=session["theirs_commit_id"])
                merge_commit_id = commit["id"]
            conn.execute("UPDATE edit_merge_sessions SET status='MERGED',merge_commit_id=?,updated_at=? WHERE id=?", (merge_commit_id,now_iso(),merge_id))
            return self._merge_payload(conn, merge_id)


    def compare_commits(self, project_id: str, from_commit_id: str, to_commit_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            self._require_commit(conn, project_id, from_commit_id)
            self._require_commit(conn, project_id, to_commit_id)
            before = self._reconstruct_conn(conn, project_id, from_commit_id)
            after = self._reconstruct_conn(conn, project_id, to_commit_id)
            changes = state_diff(before, after)
            return {
                "project_id": project_id,
                "from_commit_id": from_commit_id,
                "to_commit_id": to_commit_id,
                "from_state_hash": state_hash(before),
                "to_state_hash": state_hash(after),
                "changes": changes,
                "change_count": len(changes),
            }

    # ------------------------------------------------------------------ graph / projections
    def history_graph(self, project_id: str, *, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        limit = max(1,min(int(limit),2000)); offset=max(0,int(offset))
        with self.db.read() as conn:
            project = self._require_project(conn, project_id)
            commits = [dict(r) for r in conn.execute("SELECT * FROM edit_commits WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (project_id,limit,offset))]
            ids = {c["id"] for c in commits}
            edges = [dict(r) for r in conn.execute("SELECT commit_id,parent_commit_id,parent_order FROM edit_commit_parents WHERE commit_id IN (SELECT id FROM edit_commits WHERE project_id=?)", (project_id,)) if r["commit_id"] in ids]
            branches = [dict(r) for r in conn.execute("SELECT id,name,head_commit_id,created_from_commit_id FROM edit_branches WHERE project_id=? ORDER BY name", (project_id,))]
            total = int(conn.execute("SELECT COUNT(*) FROM edit_commits WHERE project_id=?", (project_id,)).fetchone()[0])
            return {"project_id":project_id,"active_branch_id":project.get("active_branch_id"),"detached_commit_id":project.get("detached_commit_id"),"nodes":commits,"edges":edges,"branches":branches,"total":total,"limit":limit,"offset":offset}

    def render_projection(self, project_id: str, *, sequence_id: str, commit_id: Optional[str] = None, render_settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        with self.db.read() as conn:
            project = self._require_project(conn, project_id)
            cid = commit_id or project.get("detached_commit_id")
            if cid is None: cid = self._require_branch(conn, project_id, project["active_branch_id"])["head_commit_id"]
            commit = self._require_commit(conn, project_id, cid); state = self._reconstruct_conn(conn, project_id, cid)
            if sequence_id not in state["sequences"]: raise EditNotFoundError(f"sequence not found at commit: {sequence_id}")
            self._verify_sources(state["sources"])
            ops: list[dict[str, Any]] = []
            chain: list[str] = []; cursor: Optional[str] = cid
            while cursor:
                chain.append(cursor); parents = self._parent_ids(conn,cursor); cursor = parents[0] if parents else None
            for cc in reversed(chain):
                for op in self._operation_rows(conn,cc):
                    if op.get("sequence_id") in (None, sequence_id) or op["operation_type"] == "APPLY_STATE_PATCH":
                        ops.append({k:v for k,v in op.items() if k not in {"down_payload"}})
            settings = copy.deepcopy(render_settings or {})
            projection = {
                "schema_version":1,"project_id":project_id,"sequence_id":sequence_id,"commit_id":cid,
                "state_hash":commit["state_hash"],"source_references":copy.deepcopy(state["sources"]),
                "tracks":copy.deepcopy(state["sequences"][sequence_id].get("tracks",{})),
                "track_order":copy.deepcopy(state["sequences"][sequence_id].get("track_order",[])),
                "layout":copy.deepcopy(state["sequences"][sequence_id].get("layout")),
                "operations":ops,
                "output_settings":{
                    "width":state["sequences"][sequence_id]["width"],"height":state["sequences"][sequence_id]["height"],
                    "fps_numerator":state["sequences"][sequence_id]["fps_numerator"],"fps_denominator":state["sequences"][sequence_id]["fps_denominator"],
                    **settings,
                },
            }
            projection["projection_hash"] = hashlib.sha256(canonical_json(projection).encode()).hexdigest()
            return projection

    def _verify_sources(self, sources: list[dict[str, Any]]) -> None:
        if self.library_service is None:
            return
        for source in sources:
            try: path = self._library_path(source["media_item_id"], source.get("asset_id"))
            except Exception as exc: raise EditConflictError(f"source is missing: {source['media_item_id']}") from exc
            actual = _sha256_file(path)
            if actual != source["sha256"]:
                raise EditConflictError(f"source changed: {source['media_item_id']}")
