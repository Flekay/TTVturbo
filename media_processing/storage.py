"""Atomic, file-based persistence for media jobs and pipeline runs.

Layout::

    {root_dir}/
        media_jobs/
            {job_id}/
                job.json          <- committed
                worker.log
        pipeline_runs/
            {run_id}/
                run.json          <- committed

VOD-owned artifacts (audio, transcripts) live inside the existing VOD
directory tree managed by :mod:`vod_pipeline.storage`; this module only
owns the job/run records.

Writes are atomic: ``*.tmp`` -> ``flush`` -> ``os.replace`` -> committed
file. UUID validation and path-traversal protection mirror
:mod:`vod_pipeline.storage`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator, Optional

from storage_utils import (
    atomic_write_json,
    read_json,
    safe_record_dir,
    validate_uuid,
)

from .schemas import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    MediaJobStorageError as _MediaJobStorageError,
    PipelineRunStorageError,
)

logger = logging.getLogger("ttvturbo.media_processing.storage")

JOB_FILENAME = "job.json"
RUN_FILENAME = "run.json"
WORKER_LOG_NAME = "worker.log"
TMP_SUFFIX = ".tmp"


def _validate_uuid(value: str, kind: str) -> str:
    """Backward-compat wrapper around :func:`storage_utils.validate_uuid`."""
    error_type = _MediaJobStorageError if kind == "job" else PipelineRunStorageError
    return validate_uuid(value, kind, error_type)


def _atomic_write_json(path: Path, payload: dict, error_type: type[Exception]) -> None:
    """Backward-compat wrapper around :func:`storage_utils.atomic_write_json`."""
    kind = "job" if path.name == JOB_FILENAME else "run"
    atomic_write_json(path, payload, error_type, kind=kind)


def _read_json(path: Path, error_type: type[Exception]) -> dict:
    """Backward-compat wrapper around :func:`storage_utils.read_json`."""
    kind = "job" if path.name == JOB_FILENAME else "run"
    return read_json(path, error_type, kind=kind)


class MediaJobStorage:
    """Filesystem-backed store for media jobs."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "media_jobs"
        self.runs_dir = self.data_dir / "pipeline_runs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _job_dir(self, job_id: str) -> Path:
        return safe_record_dir(self.jobs_dir, job_id, "job", _MediaJobStorageError)

    def job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / JOB_FILENAME

    def worker_log_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / WORKER_LOG_NAME

    def _run_dir(self, run_id: str) -> Path:
        return safe_record_dir(self.runs_dir, run_id, "run", PipelineRunStorageError)

    def run_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / RUN_FILENAME

    # ------------------------------------------------------------------ jobs
    def save_job(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise _MediaJobStorageError("payload must be a dict")
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise _MediaJobStorageError(
                f"unsupported job schema_version {payload.get('schema_version')!r}"
            )
        if not payload.get("id"):
            raise _MediaJobStorageError("payload missing id")
        _atomic_write_json(self.job_path(str(payload["id"])), payload, _MediaJobStorageError)

    def load_job(self, job_id: str) -> dict:
        path = self.job_path(job_id)
        if not path.is_file():
            raise _MediaJobStorageError(f"job not found: {job_id}")
        payload = _read_json(path, _MediaJobStorageError)
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise _MediaJobStorageError(
                f"unknown job schema_version {payload.get('schema_version')!r}"
            )
        return payload

    def iter_jobs(self) -> Iterator[dict]:
        try:
            entries = list(self.jobs_dir.iterdir())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not scan media_jobs root %s: %s", self.jobs_dir, exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                _validate_uuid(entry.name, "job")
            except _MediaJobStorageError:
                continue
            path = entry / JOB_FILENAME
            if not path.is_file():
                continue
            try:
                payload = _read_json(path, _MediaJobStorageError)
            except _MediaJobStorageError as exc:
                logger.warning("Skipping unreadable media job %s: %s", path, exc)
                continue
            if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                logger.warning("Skipping media job %s: unknown schema_version", path)
                continue
            yield payload

    def delete_job(self, job_id: str) -> bool:
        import shutil

        job_dir = self._job_dir(job_id)
        if not job_dir.exists():
            return False
        tmp = job_dir.with_name(job_dir.name + ".deleting")
        try:
            os.replace(job_dir, tmp)
        except OSError as exc:
            raise _MediaJobStorageError(f"could not delete job {job_id}: {exc}") from exc
        shutil.rmtree(tmp, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ runs
    def save_run(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise PipelineRunStorageError("payload must be a dict")
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise PipelineRunStorageError(
                f"unsupported run schema_version {payload.get('schema_version')!r}"
            )
        if not payload.get("id"):
            raise PipelineRunStorageError("payload missing id")
        _atomic_write_json(self.run_path(str(payload["id"])), payload, PipelineRunStorageError)

    def load_run(self, run_id: str) -> dict:
        path = self.run_path(run_id)
        if not path.is_file():
            raise PipelineRunStorageError(f"run not found: {run_id}")
        payload = _read_json(path, PipelineRunStorageError)
        if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise PipelineRunStorageError(
                f"unknown run schema_version {payload.get('schema_version')!r}"
            )
        return payload

    def iter_runs(self) -> Iterator[dict]:
        try:
            entries = list(self.runs_dir.iterdir())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not scan pipeline_runs root %s: %s", self.runs_dir, exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                _validate_uuid(entry.name, "run")
            except _MediaJobStorageError:
                continue
            path = entry / RUN_FILENAME
            if not path.is_file():
                continue
            try:
                payload = _read_json(path, PipelineRunStorageError)
            except PipelineRunStorageError as exc:
                logger.warning("Skipping unreadable pipeline run %s: %s", path, exc)
                continue
            if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                logger.warning("Skipping pipeline run %s: unknown schema_version", path)
                continue
            yield payload

    def delete_run(self, run_id: str) -> bool:
        import shutil

        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return False
        tmp = run_dir.with_name(run_dir.name + ".deleting")
        try:
            os.replace(run_dir, tmp)
        except OSError as exc:
            raise PipelineRunStorageError(f"could not delete run {run_id}: {exc}") from exc
        shutil.rmtree(tmp, ignore_errors=True)
        return True
