"""Atomic, file-based persistence for Visual Analysis.

Mirrors the safety guarantees of the other storage modules:
* ids must be valid canonical UUIDs (path-traversal protection);
* the resolved directory must stay inside the visual-analysis root;
* corrupt JSON files are logged and skipped during listing;
* atomic writes via unique ``.tmp`` -> ``os.replace``.

Layout::

    {root}/
        jobs/{job_id}/job.json
        artifacts/{artifact_id}/artifact.json
        templates/{template_id}/template.json
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

from .schemas import (
    SCHEMA_VERSION,
    VisualAnalysisNotFoundError,
    VisualAnalysisStorageError,
)

logger = logging.getLogger("ttvturbo.visual_analysis.storage")

JOB_FILENAME = "job.json"
ARTIFACT_FILENAME = "artifact.json"
TEMPLATE_FILENAME = "template.json"

JOBS_SUBDIR = "jobs"
ARTIFACTS_SUBDIR = "artifacts"
TEMPLATES_SUBDIR = "templates"


class VisualAnalysisStorage:
    """Filesystem-backed store for visual-analysis jobs, artifacts and templates."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.jobs_dir = self.root / JOBS_SUBDIR
        self.artifacts_dir = self.root / ARTIFACTS_SUBDIR
        self.templates_dir = self.root / TEMPLATES_SUBDIR
        for d in (self.jobs_dir, self.artifacts_dir, self.templates_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _job_dir(self, job_id: str) -> Path:
        return safe_record_dir(self.jobs_dir, job_id, "job", VisualAnalysisStorageError)

    def _artifact_dir(self, artifact_id: str) -> Path:
        return safe_record_dir(self.artifacts_dir, artifact_id, "artifact", VisualAnalysisStorageError)

    def _template_dir(self, template_id: str) -> Path:
        return safe_record_dir(self.templates_dir, template_id, "template", VisualAnalysisStorageError)

    def job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / JOB_FILENAME

    def artifact_path(self, artifact_id: str) -> Path:
        return self._artifact_dir(artifact_id) / ARTIFACT_FILENAME

    def template_path(self, template_id: str) -> Path:
        return self._template_dir(template_id) / TEMPLATE_FILENAME

    def keyframes_dir(self, job_id: str) -> Path:
        d = self._job_dir(job_id) / "keyframes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------ jobs
    def save_job(self, job: dict[str, Any]) -> None:
        if not isinstance(job, dict):
            raise VisualAnalysisStorageError("job must be a dict")
        if not job.get("id"):
            raise VisualAnalysisStorageError("job missing id")
        atomic_write_json(
            self.job_path(str(job["id"])),
            job,
            VisualAnalysisStorageError,
            kind="va-job",
        )

    def load_job(self, job_id: str) -> dict[str, Any]:
        validate_uuid(job_id, "job", VisualAnalysisStorageError)
        path = self.job_path(job_id)
        if not path.is_file():
            raise VisualAnalysisNotFoundError(f"visual-analysis job not found: {job_id}")
        return read_json(path, VisualAnalysisStorageError, kind="va-job")

    def iter_jobs(self) -> Iterator[dict[str, Any]]:
        try:
            entries = list(self.jobs_dir.iterdir())
        except OSError:
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            jp = sub / JOB_FILENAME
            if not jp.is_file():
                continue
            try:
                yield read_json(jp, VisualAnalysisStorageError, kind="va-job")
            except (VisualAnalysisStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt job file: %s", jp)
                continue

    def delete_job(self, job_id: str) -> bool:
        import shutil

        validate_uuid(job_id, "job", VisualAnalysisStorageError)
        d = self._job_dir(job_id)
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ artifacts
    def save_artifact(self, artifact: dict[str, Any]) -> None:
        if not isinstance(artifact, dict):
            raise VisualAnalysisStorageError("artifact must be a dict")
        if not artifact.get("id"):
            raise VisualAnalysisStorageError("artifact missing id")
        atomic_write_json(
            self.artifact_path(str(artifact["id"])),
            artifact,
            VisualAnalysisStorageError,
            kind="va-artifact",
        )

    def load_artifact(self, artifact_id: str) -> dict[str, Any]:
        validate_uuid(artifact_id, "artifact", VisualAnalysisStorageError)
        path = self.artifact_path(artifact_id)
        if not path.is_file():
            raise VisualAnalysisNotFoundError(
                f"visual-analysis artifact not found: {artifact_id}"
            )
        return read_json(path, VisualAnalysisStorageError, kind="va-artifact")

    def iter_artifacts(self) -> Iterator[dict[str, Any]]:
        try:
            entries = list(self.artifacts_dir.iterdir())
        except OSError:
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            ap = sub / ARTIFACT_FILENAME
            if not ap.is_file():
                continue
            try:
                yield read_json(ap, VisualAnalysisStorageError, kind="va-artifact")
            except (VisualAnalysisStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt artifact file: %s", ap)
                continue

    # ------------------------------------------------------------------ templates
    def save_template(self, template: dict[str, Any]) -> None:
        if not isinstance(template, dict):
            raise VisualAnalysisStorageError("template must be a dict")
        if not template.get("id"):
            raise VisualAnalysisStorageError("template missing id")
        atomic_write_json(
            self.template_path(str(template["id"])),
            template,
            VisualAnalysisStorageError,
            kind="va-template",
        )

    def load_template(self, template_id: str) -> dict[str, Any]:
        validate_uuid(template_id, "template", VisualAnalysisStorageError)
        path = self.template_path(template_id)
        if not path.is_file():
            raise VisualAnalysisNotFoundError(
                f"layout template not found: {template_id}"
            )
        return read_json(path, VisualAnalysisStorageError, kind="va-template")

    def iter_templates(self) -> Iterator[dict[str, Any]]:
        try:
            entries = list(self.templates_dir.iterdir())
        except OSError:
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            tp = sub / TEMPLATE_FILENAME
            if not tp.is_file():
                continue
            try:
                yield read_json(tp, VisualAnalysisStorageError, kind="va-template")
            except (VisualAnalysisStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt template file: %s", tp)
                continue

    def delete_template(self, template_id: str) -> bool:
        import shutil

        validate_uuid(template_id, "template", VisualAnalysisStorageError)
        d = self._template_dir(template_id)
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True
