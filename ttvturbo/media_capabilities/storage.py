"""Atomic filesystem persistence shared by media capability jobs."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterator

from ttvturbo.storage_utils import atomic_write_json, read_json, safe_record_dir, validate_uuid


class CapabilityStorageError(Exception):
    pass


class CapabilityNotFoundError(Exception):
    pass


class CapabilityStorage:
    """Filesystem-backed jobs and artifacts for one capability.

    Layout::

        root/jobs/<job-id>/job.json
        root/jobs/<job-id>/worker_job.json
        root/jobs/<job-id>/result.json
        root/jobs/<job-id>/work/
        root/artifacts/<artifact-id>/artifact.json
    """

    def __init__(self, root: Path, *, label: str) -> None:
        self.root = Path(root)
        self.label = label
        self.jobs_dir = self.root / "jobs"
        self.artifacts_dir = self.root / "artifacts"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return safe_record_dir(self.jobs_dir, job_id, "job", CapabilityStorageError)

    def artifact_dir(self, artifact_id: str) -> Path:
        return safe_record_dir(self.artifacts_dir, artifact_id, "artifact", CapabilityStorageError)

    def work_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id) / "work"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def job_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def worker_job_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "worker_job.json"

    def result_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "result.json"

    def log_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "worker.log"

    def artifact_path(self, artifact_id: str) -> Path:
        return self.artifact_dir(artifact_id) / "artifact.json"

    def save_job(self, payload: dict[str, Any]) -> None:
        if not payload.get("id"):
            raise CapabilityStorageError("job missing id")
        atomic_write_json(self.job_path(str(payload["id"])), payload, CapabilityStorageError, kind=f"{self.label}-job")

    def load_job(self, job_id: str) -> dict[str, Any]:
        validate_uuid(job_id, "job", CapabilityStorageError)
        path = self.job_path(job_id)
        if not path.is_file():
            raise CapabilityNotFoundError(f"{self.label} job not found: {job_id}")
        return read_json(path, CapabilityStorageError, kind=f"{self.label}-job")

    def iter_jobs(self) -> Iterator[dict[str, Any]]:
        if not self.jobs_dir.is_dir():
            return
        for entry in self.jobs_dir.iterdir():
            path = entry / "job.json"
            if not path.is_file():
                continue
            try:
                yield read_json(path, CapabilityStorageError, kind=f"{self.label}-job")
            except Exception:
                continue

    def save_worker_job(self, job_id: str, payload: dict[str, Any]) -> None:
        atomic_write_json(self.worker_job_path(job_id), payload, CapabilityStorageError, kind=f"{self.label}-worker-job")

    def load_worker_job(self, job_id: str) -> dict[str, Any]:
        path = self.worker_job_path(job_id)
        if not path.is_file():
            raise CapabilityNotFoundError(f"worker descriptor missing for {job_id}")
        return read_json(path, CapabilityStorageError, kind=f"{self.label}-worker-job")

    def save_result(self, job_id: str, payload: dict[str, Any]) -> None:
        atomic_write_json(self.result_path(job_id), payload, CapabilityStorageError, kind=f"{self.label}-result")

    def load_result(self, job_id: str) -> dict[str, Any] | None:
        path = self.result_path(job_id)
        if not path.is_file():
            return None
        return read_json(path, CapabilityStorageError, kind=f"{self.label}-result")

    def save_artifact(self, payload: dict[str, Any]) -> None:
        if not payload.get("id"):
            raise CapabilityStorageError("artifact missing id")
        atomic_write_json(self.artifact_path(str(payload["id"])), payload, CapabilityStorageError, kind=f"{self.label}-artifact")

    def load_artifact(self, artifact_id: str) -> dict[str, Any]:
        validate_uuid(artifact_id, "artifact", CapabilityStorageError)
        path = self.artifact_path(artifact_id)
        if not path.is_file():
            raise CapabilityNotFoundError(f"{self.label} artifact not found: {artifact_id}")
        return read_json(path, CapabilityStorageError, kind=f"{self.label}-artifact")

    def iter_artifacts(self) -> Iterator[dict[str, Any]]:
        if not self.artifacts_dir.is_dir():
            return
        for entry in self.artifacts_dir.iterdir():
            path = entry / "artifact.json"
            if not path.is_file():
                continue
            try:
                yield read_json(path, CapabilityStorageError, kind=f"{self.label}-artifact")
            except Exception:
                continue

    def delete_job(self, job_id: str) -> bool:
        validate_uuid(job_id, "job", CapabilityStorageError)
        path = self.job_dir(job_id)
        if not path.is_dir():
            return False
        shutil.rmtree(path, ignore_errors=True)
        return True
