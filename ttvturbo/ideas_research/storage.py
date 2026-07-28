"""Atomic, file-based persistence for Ideas Research.

Mirrors the safety guarantees of the other storage modules
(:mod:`ttvturbo.visual_analysis.storage`):

* ids must be valid canonical UUIDs (path-traversal protection);
* the resolved directory must stay inside the ideas-research root;
* corrupt JSON files are logged and skipped during listing;
* atomic writes via unique ``.tmp`` -> ``os.replace``.

Layout::

    {root}/
        runs/{run_id}/run.json
        runs/{run_id}/sources/{source_id}.json
        topics/{topic_id}/topic.json
        ideas/{idea_id}/idea.json
        scripts/{script_id}/script.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator, Optional

from ttvturbo.storage_utils import (
    atomic_write_json,
    read_json,
    safe_record_dir,
    validate_uuid,
)

from .schemas import IdeasResearchNotFoundError, IdeasResearchStorageError

logger = logging.getLogger("ttvturbo.ideas_research.storage")

RUN_FILENAME = "run.json"
TOPIC_FILENAME = "topic.json"
IDEA_FILENAME = "idea.json"
SCRIPT_FILENAME = "script.json"
SOURCE_FILENAME_SUFFIX = ".json"

RUNS_SUBDIR = "runs"
TOPICS_SUBDIR = "topics"
IDEAS_SUBDIR = "ideas"
SCRIPTS_SUBDIR = "scripts"
SOURCES_SUBDIR = "sources"


class IdeasResearchStorage:
    """Filesystem-backed store for ideas-research runs and artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.runs_dir = self.root / RUNS_SUBDIR
        self.topics_dir = self.root / TOPICS_SUBDIR
        self.ideas_dir = self.root / IDEAS_SUBDIR
        self.scripts_dir = self.root / SCRIPTS_SUBDIR
        for d in (self.runs_dir, self.topics_dir, self.ideas_dir, self.scripts_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _run_dir(self, run_id: str) -> Path:
        return safe_record_dir(self.runs_dir, run_id, "run", IdeasResearchStorageError)

    def _topic_dir(self, topic_id: str) -> Path:
        return safe_record_dir(self.topics_dir, topic_id, "topic", IdeasResearchStorageError)

    def _idea_dir(self, idea_id: str) -> Path:
        return safe_record_dir(self.ideas_dir, idea_id, "idea", IdeasResearchStorageError)

    def _script_dir(self, script_id: str) -> Path:
        return safe_record_dir(self.scripts_dir, script_id, "script", IdeasResearchStorageError)

    def run_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / RUN_FILENAME

    def sources_dir(self, run_id: str) -> Path:
        d = self._run_dir(run_id) / SOURCES_SUBDIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    def source_path(self, run_id: str, source_id: str) -> Path:
        validate_uuid(source_id, "source", IdeasResearchStorageError)
        return self.sources_dir(run_id) / f"{source_id}{SOURCE_FILENAME_SUFFIX}"

    def topic_path(self, topic_id: str) -> Path:
        return self._topic_dir(topic_id) / TOPIC_FILENAME

    def idea_path(self, idea_id: str) -> Path:
        return self._idea_dir(idea_id) / IDEA_FILENAME

    def script_path(self, script_id: str) -> Path:
        return self._script_dir(script_id) / SCRIPT_FILENAME

    # ------------------------------------------------------------------ runs
    def save_run(self, run: dict[str, Any]) -> None:
        if not isinstance(run, dict):
            raise IdeasResearchStorageError("run must be a dict")
        if not run.get("id"):
            raise IdeasResearchStorageError("run missing id")
        atomic_write_json(
            self.run_path(str(run["id"])),
            run,
            IdeasResearchStorageError,
            kind="ir-run",
        )

    def load_run(self, run_id: str) -> dict[str, Any]:
        validate_uuid(run_id, "run", IdeasResearchStorageError)
        path = self.run_path(run_id)
        if not path.is_file():
            raise IdeasResearchNotFoundError(f"ideas-research run not found: {run_id}")
        return read_json(path, IdeasResearchStorageError, kind="ir-run")

    def iter_runs(self) -> Iterator[dict[str, Any]]:
        try:
            entries = list(self.runs_dir.iterdir())
        except OSError:
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            rp = sub / RUN_FILENAME
            if not rp.is_file():
                continue
            try:
                yield read_json(rp, IdeasResearchStorageError, kind="ir-run")
            except (IdeasResearchStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt run file: %s", rp)
                continue

    # ------------------------------------------------------------------ sources
    def save_source(self, run_id: str, source: dict[str, Any]) -> None:
        if not isinstance(source, dict):
            raise IdeasResearchStorageError("source must be a dict")
        if not source.get("id"):
            raise IdeasResearchStorageError("source missing id")
        atomic_write_json(
            self.source_path(run_id, str(source["id"])),
            source,
            IdeasResearchStorageError,
            kind="ir-source",
        )

    def load_source(self, run_id: str, source_id: str) -> dict[str, Any]:
        path = self.source_path(run_id, source_id)
        if not path.is_file():
            raise IdeasResearchNotFoundError(f"source not found: {source_id}")
        return read_json(path, IdeasResearchStorageError, kind="ir-source")

    def iter_sources(self, run_id: str) -> Iterator[dict[str, Any]]:
        d = self.sources_dir(run_id)
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for sp in entries:
            if not sp.is_file() or sp.suffix != ".json":
                continue
            try:
                yield read_json(sp, IdeasResearchStorageError, kind="ir-source")
            except (IdeasResearchStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt source file: %s", sp)
                continue

    # ------------------------------------------------------------------ topics
    def save_topic(self, topic: dict[str, Any]) -> None:
        if not isinstance(topic, dict):
            raise IdeasResearchStorageError("topic must be a dict")
        if not topic.get("id"):
            raise IdeasResearchStorageError("topic missing id")
        atomic_write_json(
            self.topic_path(str(topic["id"])),
            topic,
            IdeasResearchStorageError,
            kind="ir-topic",
        )

    def load_topic(self, topic_id: str) -> dict[str, Any]:
        validate_uuid(topic_id, "topic", IdeasResearchStorageError)
        path = self.topic_path(topic_id)
        if not path.is_file():
            raise IdeasResearchNotFoundError(f"topic not found: {topic_id}")
        return read_json(path, IdeasResearchStorageError, kind="ir-topic")

    def iter_topics(self) -> Iterator[dict[str, Any]]:
        try:
            entries = list(self.topics_dir.iterdir())
        except OSError:
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            tp = sub / TOPIC_FILENAME
            if not tp.is_file():
                continue
            try:
                yield read_json(tp, IdeasResearchStorageError, kind="ir-topic")
            except (IdeasResearchStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt topic file: %s", tp)
                continue

    # ------------------------------------------------------------------ ideas
    def save_idea(self, idea: dict[str, Any]) -> None:
        if not isinstance(idea, dict):
            raise IdeasResearchStorageError("idea must be a dict")
        if not idea.get("id"):
            raise IdeasResearchStorageError("idea missing id")
        atomic_write_json(
            self.idea_path(str(idea["id"])),
            idea,
            IdeasResearchStorageError,
            kind="ir-idea",
        )

    def load_idea(self, idea_id: str) -> dict[str, Any]:
        validate_uuid(idea_id, "idea", IdeasResearchStorageError)
        path = self.idea_path(idea_id)
        if not path.is_file():
            raise IdeasResearchNotFoundError(f"idea not found: {idea_id}")
        return read_json(path, IdeasResearchStorageError, kind="ir-idea")

    def iter_ideas(self) -> Iterator[dict[str, Any]]:
        try:
            entries = list(self.ideas_dir.iterdir())
        except OSError:
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            ip = sub / IDEA_FILENAME
            if not ip.is_file():
                continue
            try:
                yield read_json(ip, IdeasResearchStorageError, kind="ir-idea")
            except (IdeasResearchStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt idea file: %s", ip)
                continue

    # ------------------------------------------------------------------ scripts
    def save_script(self, script: dict[str, Any]) -> None:
        if not isinstance(script, dict):
            raise IdeasResearchStorageError("script must be a dict")
        if not script.get("id"):
            raise IdeasResearchStorageError("script missing id")
        atomic_write_json(
            self.script_path(str(script["id"])),
            script,
            IdeasResearchStorageError,
            kind="ir-script",
        )

    def load_script(self, script_id: str) -> dict[str, Any]:
        validate_uuid(script_id, "script", IdeasResearchStorageError)
        path = self.script_path(script_id)
        if not path.is_file():
            raise IdeasResearchNotFoundError(f"script not found: {script_id}")
        return read_json(path, IdeasResearchStorageError, kind="ir-script")

    def iter_scripts(self) -> Iterator[dict[str, Any]]:
        try:
            entries = list(self.scripts_dir.iterdir())
        except OSError:
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            sp = sub / SCRIPT_FILENAME
            if not sp.is_file():
                continue
            try:
                yield read_json(sp, IdeasResearchStorageError, kind="ir-script")
            except (IdeasResearchStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt script file: %s", sp)
                continue
