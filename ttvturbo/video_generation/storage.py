"""Atomic, file-based persistence for Video Generation.

Mirrors the safety guarantees of the other storage modules
(:mod:`visual_analysis.storage`, :mod:`library.storage`):

* ids must be valid canonical UUIDs (path-traversal protection);
* the resolved directory must stay inside the video-generation root;
* corrupt JSON files are logged and skipped during listing;
* atomic writes via unique ``.tmp`` -> ``os.replace``.

Layout::

    {root}/
        jobs/{job_id}/
            job.json
            worker_job.json
            worker.log
            source_image.{ext}   # copied input image for I2V
            output.mp4           # generated video (worker-written)
            result.json          # worker-written result summary
        artifacts/{artifact_id}/artifact.json
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
    VideoGenerationNotFoundError,
    VideoGenerationStorageError,
)

logger = logging.getLogger("ttvturbo.video_generation.storage")

JOB_FILENAME = "job.json"
WORKER_JOB_FILENAME = "worker_job.json"
WORKER_LOG = "worker.log"
RESULT_FILENAME = "result.json"
OUTPUT_FILENAME = "output.mp4"
SOURCE_IMAGE_BASENAME = "source_image"
ARTIFACT_FILENAME = "artifact.json"

JOBS_SUBDIR = "jobs"
ARTIFACTS_SUBDIR = "artifacts"


class VideoGenerationStorage:
    """Filesystem-backed store for video-generation jobs and artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.jobs_dir = self.root / JOBS_SUBDIR
        self.artifacts_dir = self.root / ARTIFACTS_SUBDIR
        for d in (self.jobs_dir, self.artifacts_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _job_dir(self, job_id: str) -> Path:
        return safe_record_dir(self.jobs_dir, job_id, "job", VideoGenerationStorageError)

    def _artifact_dir(self, artifact_id: str) -> Path:
        return safe_record_dir(
            self.artifacts_dir, artifact_id, "artifact", VideoGenerationStorageError
        )

    def job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / JOB_FILENAME

    def worker_job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / WORKER_JOB_FILENAME

    def worker_log_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / WORKER_LOG

    def result_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / RESULT_FILENAME

    def output_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / OUTPUT_FILENAME

    def artifact_path(self, artifact_id: str) -> Path:
        return self._artifact_dir(artifact_id) / ARTIFACT_FILENAME

    # ------------------------------------------------------------------ jobs
    def save_job(self, job: dict[str, Any]) -> None:
        if not isinstance(job, dict):
            raise VideoGenerationStorageError("job must be a dict")
        if not job.get("id"):
            raise VideoGenerationStorageError("job missing id")
        atomic_write_json(
            self.job_path(str(job["id"])),
            job,
            VideoGenerationStorageError,
            kind="vg-job",
        )

    def load_job(self, job_id: str) -> dict[str, Any]:
        validate_uuid(job_id, "job", VideoGenerationStorageError)
        path = self.job_path(job_id)
        if not path.is_file():
            raise VideoGenerationNotFoundError(
                f"video-generation job not found: {job_id}"
            )
        return read_json(path, VideoGenerationStorageError, kind="vg-job")

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
                yield read_json(jp, VideoGenerationStorageError, kind="vg-job")
            except (VideoGenerationStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt job file: %s", jp)
                continue

    def delete_job(self, job_id: str) -> bool:
        import shutil

        validate_uuid(job_id, "job", VideoGenerationStorageError)
        d = self._job_dir(job_id)
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True

    # ------------------------------------------------------- worker artifacts
    def save_worker_job(self, job_id: str, worker_job: dict[str, Any]) -> None:
        validate_uuid(job_id, "job", VideoGenerationStorageError)
        atomic_write_json(
            self.worker_job_path(job_id),
            worker_job,
            VideoGenerationStorageError,
            kind="vg-worker-job",
        )

    def load_result(self, job_id: str) -> Optional[dict[str, Any]]:
        """Load the worker-written ``result.json`` if present."""
        validate_uuid(job_id, "job", VideoGenerationStorageError)
        path = self.result_path(job_id)
        if not path.is_file():
            return None
        try:
            return read_json(path, VideoGenerationStorageError, kind="vg-result")
        except (VideoGenerationStorageError, json.JSONDecodeError):
            logger.warning("skipping corrupt result file: %s", path)
            return None

    # ------------------------------------------------------------------ artifacts
    def save_artifact(self, artifact: dict[str, Any]) -> None:
        if not isinstance(artifact, dict):
            raise VideoGenerationStorageError("artifact must be a dict")
        if not artifact.get("id"):
            raise VideoGenerationStorageError("artifact missing id")
        atomic_write_json(
            self.artifact_path(str(artifact["id"])),
            artifact,
            VideoGenerationStorageError,
            kind="vg-artifact",
        )

    def load_artifact(self, artifact_id: str) -> dict[str, Any]:
        validate_uuid(artifact_id, "artifact", VideoGenerationStorageError)
        path = self.artifact_path(artifact_id)
        if not path.is_file():
            raise VideoGenerationNotFoundError(
                f"video-generation artifact not found: {artifact_id}"
            )
        return read_json(path, VideoGenerationStorageError, kind="vg-artifact")

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
                yield read_json(ap, VideoGenerationStorageError, kind="vg-artifact")
            except (VideoGenerationStorageError, json.JSONDecodeError):
                logger.warning("skipping corrupt artifact file: %s", ap)
                continue
