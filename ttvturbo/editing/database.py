"""SQLite persistence for edit projects.

Only the editor subsystem uses this database. Other TTVturbo domains remain
file-backed. Writes use explicit transactions and SQLite foreign keys.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import EditStorageError

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS edit_projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        active_branch_id TEXT,
        active_sequence_id TEXT,
        detached_commit_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS edit_project_sources (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
        media_item_id TEXT NOT NULL,
        asset_id TEXT,
        sha256 TEXT NOT NULL,
        source_revision TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(project_id, media_item_id, asset_id)
    );
    CREATE TABLE IF NOT EXISTS edit_sequences (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        initial_width INTEGER NOT NULL,
        initial_height INTEGER NOT NULL,
        fps_numerator INTEGER NOT NULL,
        fps_denominator INTEGER NOT NULL,
        format_profile TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS edit_commits (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
        author TEXT,
        message TEXT NOT NULL,
        state_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS edit_commit_parents (
        commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE CASCADE,
        parent_commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE RESTRICT,
        parent_order INTEGER NOT NULL,
        PRIMARY KEY(commit_id, parent_order),
        UNIQUE(commit_id, parent_commit_id)
    );
    CREATE TABLE IF NOT EXISTS edit_branches (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        head_commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE RESTRICT,
        created_from_commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, name)
    );
    CREATE TABLE IF NOT EXISTS edit_operations (
        id TEXT PRIMARY KEY,
        commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE CASCADE,
        sequence_id TEXT,
        operation_order INTEGER NOT NULL,
        operation_type TEXT NOT NULL,
        entity_id TEXT,
        up_payload_json TEXT NOT NULL,
        down_payload_json TEXT NOT NULL,
        affected_path TEXT,
        start_us INTEGER,
        end_us INTEGER,
        UNIQUE(commit_id, operation_order)
    );
    CREATE TABLE IF NOT EXISTS edit_snapshots (
        commit_id TEXT PRIMARY KEY REFERENCES edit_commits(id) ON DELETE CASCADE,
        state_json TEXT NOT NULL,
        state_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS edit_merge_sessions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
        source_branch_id TEXT NOT NULL REFERENCES edit_branches(id) ON DELETE CASCADE,
        target_branch_id TEXT NOT NULL REFERENCES edit_branches(id) ON DELETE CASCADE,
        merge_base_commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE RESTRICT,
        ours_commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE RESTRICT,
        theirs_commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE RESTRICT,
        status TEXT NOT NULL,
        merged_state_json TEXT NOT NULL,
        merge_commit_id TEXT REFERENCES edit_commits(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS edit_merge_conflicts (
        id TEXT PRIMARY KEY,
        merge_id TEXT NOT NULL REFERENCES edit_merge_sessions(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        kind TEXT NOT NULL,
        base_payload_json TEXT,
        ours_payload_json TEXT,
        theirs_payload_json TEXT,
        resolution TEXT,
        resolved_payload_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(merge_id, path)
    );
    CREATE TABLE IF NOT EXISTS edit_render_artifacts (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
        sequence_id TEXT NOT NULL,
        commit_id TEXT NOT NULL REFERENCES edit_commits(id) ON DELETE RESTRICT,
        state_hash TEXT NOT NULL,
        render_settings_hash TEXT NOT NULL,
        artifact_id TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_edit_commits_project_created ON edit_commits(project_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_edit_branches_project ON edit_branches(project_id);
    CREATE INDEX IF NOT EXISTS idx_edit_operations_commit ON edit_operations(commit_id, operation_order);
    CREATE INDEX IF NOT EXISTS idx_edit_parents_parent ON edit_commit_parents(parent_commit_id);
    """),
)


class EditDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            return conn
        except sqlite3.Error as exc:
            raise EditStorageError(f"could not open edit database: {exc}") from exc

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")}
            from ttvturbo.storage_utils import now_iso
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                # executescript normally commits implicitly. Wrap each migration
                # explicitly so a failing statement cannot leave a half-applied
                # editor schema behind.
                try:
                    conn.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")
                except sqlite3.Error:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, now_iso()),
                )
        except sqlite3.Error as exc:
            raise EditStorageError(f"could not migrate edit database: {exc}") from exc
        finally:
            conn.close()
