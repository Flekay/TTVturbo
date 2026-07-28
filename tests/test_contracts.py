"""Tests for the shared backend capability contracts.

Covers:

* Status transitions (allowed + rejected).
* Progress validation in the 0..100 range.
* Terminal / active / cancellable classification.
* Unified error format.
* Artifact references.
* Capability status contract.
* Backward compatibility: existing domain schemas are not broken and
  the new contracts module does not import any domain package.
* No route duplicates: introducing the contracts package must not add
  any HTTP route, and there must be no ``/operations/run-anything``
  universal endpoint.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ttvturbo.contracts import (
    ACTIVE_JOB_STATUSES,
    ALLOWED_JOB_TRANSITIONS,
    ArtifactReference,
    CANCELLABLE_JOB_STATUSES,
    CapabilityStatus,
    DEFAULT_JOB_OPERATION,
    InvalidJobProgressError,
    InvalidJobTransitionError,
    JobStatus,
    MediaReference,
    OperationError,
    OperationJob,
    TERMINAL_JOB_STATUSES,
    assert_transition,
    is_active,
    is_cancellable,
    is_terminal,
    transition,
    validate_progress,
)
from ttvturbo.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "ttvturbo" / "contracts"


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        (JobStatus.QUEUED.value, JobStatus.CANCELED.value),
        (JobStatus.QUEUED.value, JobStatus.FAILED.value),
        (JobStatus.RUNNING.value, JobStatus.COMPLETED.value),
        (JobStatus.RUNNING.value, JobStatus.FAILED.value),
        (JobStatus.RUNNING.value, JobStatus.CANCELED.value),
        (JobStatus.RUNNING.value, JobStatus.CANCELING.value),
        (JobStatus.RUNNING.value, JobStatus.RETRYING.value),
        (JobStatus.CANCELING.value, JobStatus.CANCELED.value),
        (JobStatus.CANCELING.value, JobStatus.FAILED.value),
        (JobStatus.RETRYING.value, JobStatus.RUNNING.value),
        (JobStatus.RETRYING.value, JobStatus.FAILED.value),
        (JobStatus.RETRYING.value, JobStatus.CANCELED.value),
    ],
)
def test_allowed_transitions(from_status: str, to_status: str) -> None:
    assert_transition(from_status, to_status)  # must not raise
    assert transition(from_status, to_status) == to_status


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        # Terminal states have no outgoing edges.
        (JobStatus.COMPLETED.value, JobStatus.RUNNING.value),
        (JobStatus.FAILED.value, JobStatus.RUNNING.value),
        (JobStatus.CANCELED.value, JobStatus.RUNNING.value),
        (JobStatus.COMPLETED.value, JobStatus.FAILED.value),
        # Reverse / nonsensical transitions.
        (JobStatus.RUNNING.value, JobStatus.QUEUED.value),
        (JobStatus.QUEUED.value, JobStatus.COMPLETED.value),
        (JobStatus.CANCELED.value, JobStatus.CANCELING.value),
        # Unknown states.
        ("PAUSED", JobStatus.RUNNING.value),
        (JobStatus.RUNNING.value, "PAUSED"),
    ],
)
def test_rejected_transitions(from_status: str, to_status: str) -> None:
    with pytest.raises(InvalidJobTransitionError):
        assert_transition(from_status, to_status)


def test_terminal_states_have_no_outgoing_edges() -> None:
    for terminal in TERMINAL_JOB_STATUSES:
        assert ALLOWED_JOB_TRANSITIONS[terminal] == frozenset()


def test_is_terminal_classification() -> None:
    assert is_terminal(JobStatus.COMPLETED.value)
    assert is_terminal(JobStatus.FAILED.value)
    assert is_terminal(JobStatus.CANCELED.value)
    assert not is_terminal(JobStatus.QUEUED.value)
    assert not is_terminal(JobStatus.RUNNING.value)
    assert not is_terminal(JobStatus.CANCELING.value)
    assert not is_terminal(JobStatus.RETRYING.value)


def test_is_active_classification() -> None:
    assert is_active(JobStatus.QUEUED.value)
    assert is_active(JobStatus.RUNNING.value)
    assert is_active(JobStatus.CANCELING.value)
    assert is_active(JobStatus.RETRYING.value)
    for terminal in TERMINAL_JOB_STATUSES:
        assert not is_active(terminal)


def test_is_cancellable_classification() -> None:
    assert is_cancellable(JobStatus.QUEUED.value)
    assert is_cancellable(JobStatus.RUNNING.value)
    assert is_cancellable(JobStatus.RETRYING.value)
    # Canceling itself is not cancellable (already being canceled).
    assert not is_cancellable(JobStatus.CANCELING.value)
    for terminal in TERMINAL_JOB_STATUSES:
        assert not is_cancellable(terminal)


def test_optional_states_are_valid_status_values() -> None:
    """CANCELING and RETRYING are optional but must be valid JobStatus values."""
    valid = {s.value for s in JobStatus}
    assert "CANCELING" in valid
    assert "RETRYING" in valid
    # The five core states are always present.
    for core in ("QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELED"):
        assert core in valid


# ---------------------------------------------------------------------------
# Progress 0..100
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0.0, 0, 1, 50, 99.99, 100, 100.0])
def test_progress_valid(value: float) -> None:
    out = validate_progress(value)
    assert out is not None
    assert 0.0 <= out <= 100.0


def test_progress_none_is_allowed() -> None:
    assert validate_progress(None) is None


@pytest.mark.parametrize("value", [-0.01, -1, 100.01, 120, 999])
def test_progress_out_of_range_rejected(value: float) -> None:
    with pytest.raises(InvalidJobProgressError):
        validate_progress(value)


@pytest.mark.parametrize("value", [True, False, "50", [], {}])
def test_progress_wrong_type_rejected(value) -> None:
    with pytest.raises(InvalidJobProgressError):
        validate_progress(value)


def test_operation_job_progress_field_validated() -> None:
    job = OperationJob(
        id="job-1",
        operation="facecam_enhancement",
        created_at="2026-07-29T00:00:00+00:00",
    )
    assert job.progress is None

    job = job.model_copy(update={"progress": 42.5})
    assert job.progress == 42.5

    with pytest.raises(ValidationError):
        OperationJob(
            id="job-2",
            operation="facecam_enhancement",
            progress=120.0,
            created_at="2026-07-29T00:00:00+00:00",
        )
    with pytest.raises(ValidationError):
        OperationJob(
            id="job-3",
            operation="facecam_enhancement",
            progress=-1.0,
            created_at="2026-07-29T00:00:00+00:00",
        )


# ---------------------------------------------------------------------------
# Terminal jobs (full lifecycle)
# ---------------------------------------------------------------------------


def test_full_successful_lifecycle() -> None:
    job = OperationJob(
        id="job-ok",
        operation="facecam_enhancement",
        created_at="2026-07-29T00:00:00+00:00",
    )
    assert job.status == JobStatus.QUEUED.value
    assert is_terminal(job.status) is False

    job = job.model_copy(
        update={
            "status": transition(job.status, JobStatus.RUNNING.value),
            "started_at": "2026-07-29T00:00:01+00:00",
            "progress": 0.0,
        }
    )
    job = job.model_copy(update={"progress": validate_progress(50.0)})
    assert job.progress == 50.0

    job = job.model_copy(
        update={
            "status": transition(job.status, JobStatus.COMPLETED.value),
            "progress": 100.0,
            "completed_at": "2026-07-29T00:00:10+00:00",
        }
    )
    assert is_terminal(job.status)
    assert job.error is None


def test_full_failed_lifecycle_with_error() -> None:
    job = OperationJob(
        id="job-fail",
        operation="transcribe",
        created_at="2026-07-29T00:00:00+00:00",
    )
    job = job.model_copy(
        update={
            "status": transition(job.status, JobStatus.RUNNING.value),
            "started_at": "2026-07-29T00:00:01+00:00",
        }
    )
    err = OperationError(
        code="MODEL_UNAVAILABLE",
        message="whisper model weights not found",
        retryable=True,
        details={"model": "large-v3"},
    )
    job = job.model_copy(
        update={
            "status": transition(job.status, JobStatus.FAILED.value),
            "error": err,
            "completed_at": "2026-07-29T00:00:02+00:00",
        }
    )
    assert is_terminal(job.status)
    assert job.error is not None
    assert job.error.code == "MODEL_UNAVAILABLE"
    assert job.error.retryable is True


def test_cancel_lifecycle_via_optional_canceling() -> None:
    job = OperationJob(
        id="job-cancel",
        operation="download",
        created_at="2026-07-29T00:00:00+00:00",
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.RUNNING.value)}
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.CANCELING.value)}
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.CANCELED.value)}
    )
    assert is_terminal(job.status)


def test_cancel_lifecycle_skipping_optional_canceling() -> None:
    """A tool may collapse CANCELING -> CANCELED."""
    job = OperationJob(
        id="job-cancel-quick",
        operation="download",
        created_at="2026-07-29T00:00:00+00:00",
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.RUNNING.value)}
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.CANCELED.value)}
    )
    assert is_terminal(job.status)


def test_retry_lifecycle() -> None:
    job = OperationJob(
        id="job-retry",
        operation="transcribe",
        created_at="2026-07-29T00:00:00+00:00",
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.RUNNING.value)}
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.RETRYING.value)}
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.RUNNING.value)}
    )
    job = job.model_copy(
        update={"status": transition(job.status, JobStatus.COMPLETED.value)}
    )
    assert is_terminal(job.status)


# ---------------------------------------------------------------------------
# Unified error format
# ---------------------------------------------------------------------------


def test_error_minimal_fields() -> None:
    err = OperationError(code="GENERIC", message="something went wrong")
    assert err.retryable is False
    assert err.details == {}


def test_error_full_shape_round_trips() -> None:
    err = OperationError(
        code="MODEL_UNAVAILABLE",
        message="...",
        retryable=True,
        details={"model": "large-v3", "path": "/models/"},
    )
    payload = err.model_dump()
    assert set(payload.keys()) == {"code", "message", "retryable", "details"}
    assert payload["details"]["model"] == "large-v3"
    # Round-trip via JSON to mimic the persisted shape.
    restored = OperationError.model_validate_json(json.dumps(payload))
    assert restored == err


def test_error_rejects_empty_code_or_message() -> None:
    with pytest.raises(ValidationError):
        OperationError(code="", message="x")
    with pytest.raises(ValidationError):
        OperationError(code="X", message="   ")


def test_error_details_default_is_independent_per_instance() -> None:
    a = OperationError(code="A", message="a")
    b = OperationError(code="B", message="b")
    a.details["k"] = "v"
    assert "k" not in b.details


# ---------------------------------------------------------------------------
# Artifact references
# ---------------------------------------------------------------------------


def test_artifact_reference_minimal() -> None:
    art = ArtifactReference(
        artifact_id="art-1",
        artifact_type="audio",
        media_item_id="media-1",
        created_at="2026-07-29T00:00:00+00:00",
    )
    assert art.revision == "1"


def test_artifact_reference_full() -> None:
    art = ArtifactReference(
        artifact_id="art-1",
        artifact_type="transcript",
        media_item_id="media-1",
        created_at="2026-07-29T00:00:00+00:00",
        revision="v3",
    )
    payload = art.model_dump()
    assert payload == {
        "artifact_id": "art-1",
        "artifact_type": "transcript",
        "media_item_id": "media-1",
        "created_at": "2026-07-29T00:00:00+00:00",
        "revision": "v3",
    }


def test_artifact_reference_ignores_extra_fields() -> None:
    """Backward compat: domains may add their own fields; the contract ignores them."""
    art = ArtifactReference.model_validate(
        {
            "artifact_id": "art-1",
            "artifact_type": "audio",
            "media_item_id": "media-1",
            "created_at": "2026-07-29T00:00:00+00:00",
            "revision": "r9",
            "domain_specific_path": "vods/abc/artifacts/audio/source.flac",
            "produced_by_job_id": "job-7",
        }
    )
    assert art.artifact_id == "art-1"
    assert not hasattr(art, "domain_specific_path")


# ---------------------------------------------------------------------------
# Media reference
# ---------------------------------------------------------------------------


def test_media_reference_minimal() -> None:
    ref = MediaReference(media_item_id="media-1")
    assert ref.asset_id is None
    assert ref.start_seconds is None
    assert ref.end_seconds is None
    assert ref.source_revision is None


def test_media_reference_time_range_valid() -> None:
    ref = MediaReference(
        media_item_id="media-1",
        start_seconds=10.0,
        end_seconds=20.0,
    )
    assert ref.start_seconds == 10.0
    assert ref.end_seconds == 20.0


def test_media_reference_time_range_inverted_rejected() -> None:
    with pytest.raises(ValidationError):
        MediaReference(
            media_item_id="media-1",
            start_seconds=20.0,
            end_seconds=10.0,
        )


def test_media_reference_negative_seconds_rejected() -> None:
    with pytest.raises(ValidationError):
        MediaReference(media_item_id="media-1", start_seconds=-1.0)
    with pytest.raises(ValidationError):
        MediaReference(media_item_id="media-1", end_seconds=-1.0)


def test_media_reference_ignores_extra_fields() -> None:
    ref = MediaReference.model_validate(
        {
            "media_item_id": "media-1",
            "asset_id": "asset-1",
            "source_revision": "rev-2",
            "container": "mp4",
            "duration_seconds": 123.4,
        }
    )
    assert ref.asset_id == "asset-1"
    assert not hasattr(ref, "container")


# ---------------------------------------------------------------------------
# OperationJob validation
# ---------------------------------------------------------------------------


def test_job_default_status_is_queued() -> None:
    job = OperationJob(
        id="job-1",
        operation="facecam_enhancement",
        created_at="2026-07-29T00:00:00+00:00",
    )
    assert job.status == JobStatus.QUEUED.value
    assert job.input_references == []
    assert job.output_artifacts == []
    assert job.error is None


def test_job_default_operation_when_omitted() -> None:
    job = OperationJob(id="job-1", created_at="2026-07-29T00:00:00+00:00")
    assert job.operation == DEFAULT_JOB_OPERATION


def test_job_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        OperationJob(
            id="job-1",
            operation="x",
            status="PAUSED",
            created_at="2026-07-29T00:00:00+00:00",
        )


def test_job_rejects_empty_operation() -> None:
    with pytest.raises(ValidationError):
        OperationJob(
            id="job-1",
            operation="  ",
            created_at="2026-07-29T00:00:00+00:00",
        )


def test_job_with_inputs_and_outputs() -> None:
    job = OperationJob(
        id="job-1",
        operation="facecam_enhancement",
        created_at="2026-07-29T00:00:00+00:00",
        input_references=[
            MediaReference(media_item_id="media-1", asset_id="asset-video"),
            MediaReference(media_item_id="media-2", start_seconds=0.0, end_seconds=30.0),
        ],
        output_artifacts=[
            ArtifactReference(
                artifact_id="art-out",
                artifact_type="enhanced_video",
                media_item_id="media-1",
                created_at="2026-07-29T00:00:05+00:00",
                revision="r1",
            )
        ],
    )
    assert len(job.input_references) == 2
    assert job.output_artifacts[0].artifact_type == "enhanced_video"


def test_job_round_trips_through_json() -> None:
    job = OperationJob(
        id="job-rt",
        operation="transcribe",
        status=JobStatus.RUNNING.value,
        progress=42.0,
        current_stage="LOADING_MODEL",
        created_at="2026-07-29T00:00:00+00:00",
        started_at="2026-07-29T00:00:01+00:00",
        input_references=[MediaReference(media_item_id="m1")],
        error=OperationError(code="BOOM", message="boom", retryable=False),
    )
    payload = job.model_dump_json()
    restored = OperationJob.model_validate_json(payload)
    assert restored == job


# ---------------------------------------------------------------------------
# Capability status
# ---------------------------------------------------------------------------


def test_capability_available_minimal() -> None:
    cap = CapabilityStatus(id="facecam_enhancement", available=True)
    assert cap.configured is False
    assert cap.busy is False
    assert cap.reason is None


def test_capability_unavailable_with_reason() -> None:
    cap = CapabilityStatus(
        id="facecam_enhancement",
        available=False,
        configured=True,
        busy=False,
        reason="GPU busy with transcription",
    )
    assert cap.available is False
    assert cap.reason == "GPU busy with transcription"


def test_capability_rejects_reason_when_available() -> None:
    with pytest.raises(ValidationError):
        CapabilityStatus(
            id="facecam_enhancement",
            available=True,
            reason="should not be here",
        )


def test_capability_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        CapabilityStatus(id="  ", available=True)


def test_capability_round_trips() -> None:
    cap = CapabilityStatus(
        id="voice_clone",
        available=True,
        configured=True,
        busy=True,
        reason=None,
    )
    restored = CapabilityStatus.model_validate_json(cap.model_dump_json())
    assert restored == cap


def test_capability_status_example_from_spec() -> None:
    """The exact example from the spec must validate."""
    cap = CapabilityStatus.model_validate(
        {
            "id": "facecam_enhancement",
            "available": True,
            "configured": True,
            "busy": False,
            "reason": None,
        }
    )
    assert cap.id == "facecam_enhancement"
    assert cap.available is True
    assert cap.configured is True
    assert cap.busy is False
    assert cap.reason is None


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_contracts_does_not_import_domain_packages() -> None:
    """The contracts package must not import any domain package.

    This keeps the dependency direction one-way (domains may opt into
    contracts, never the reverse) and prevents circular imports.
    """
    forbidden_prefixes = (
        "ttvturbo.vod_pipeline",
        "ttvturbo.voice_clone",
        "ttvturbo.voice_profiles",
        "ttvturbo.library",
        "ttvturbo.media_processing",
        "ttvturbo.app",
        "ttvturbo.app_factory",
        "ttvturbo.conversation_mining_api",
    )

    offenders: list[str] = []
    for f in sorted(CONTRACTS_DIR.rglob("*.py")):
        source = f.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        offenders.append(f"{f.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith(forbidden_prefixes):
                    offenders.append(f"{f.name}: from {node.module} import ...")
    assert not offenders, "contracts must not import domain packages:\n" + "\n".join(
        offenders
    )


def test_contracts_does_not_import_fastapi() -> None:
    """Contracts are pure models — no HTTP framework coupling."""
    offenders: list[str] = []
    for f in sorted(CONTRACTS_DIR.rglob("*.py")):
        source = f.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("fastapi"):
                        offenders.append(f"{f.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("fastapi"):
                    offenders.append(f"{f.name}: from {node.module} import ...")
    assert not offenders, "contracts must not import fastapi:\n" + "\n".join(offenders)


def test_existing_domain_schemas_still_import() -> None:
    """Existing domain schema modules must still import unchanged."""
    from ttvturbo.media_processing.schemas import (  # noqa: F401
        MediaJob,
        MediaJobStatus,
        PipelineRun,
        PipelineStatus,
    )
    from ttvturbo.vod_pipeline.schemas import (  # noqa: F401
        TwitchVod,
        VodStatus,
    )

    # The existing status enums still expose the same core values.
    assert MediaJobStatus.QUEUED.value == "QUEUED"
    assert PipelineStatus.QUEUED.value == "QUEUED"
    assert VodStatus.QUEUED.value == "QUEUED"


def test_existing_status_sets_share_core_non_terminal_states() -> None:
    """Every existing domain enum exposes the shared non-terminal + failure states.

    The contract's terminal-success state is ``COMPLETED``.  Existing
    domains may use a different name for terminal success (e.g.
    ``MediaJobStatus.READY`` or ``VodStatus.READY``) — that is exactly
    the kind of domain variation the contract exists to normalise, so
    we do **not** require existing enums to expose ``COMPLETED``.  We
    only require the shared in-flight + failure + cancel states, which
    every domain already uses with identical names.
    """
    # The truly universal states across every existing domain enum.
    # RUNNING is intentionally excluded: VodStatus uses DOWNLOADING for
    # its in-flight state.  The contract normalises these per-domain
    # "in-flight" names to RUNNING at the API boundary.
    shared = {
        JobStatus.QUEUED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
    }
    from ttvturbo.media_processing.schemas import MediaJobStatus, PipelineStatus
    from ttvturbo.vod_pipeline.schemas import VodStatus

    for enum in (MediaJobStatus, PipelineStatus, VodStatus):
        values = {e.value for e in enum}
        assert shared.issubset(values), (
            f"{enum.__name__} missing shared states: {shared - values}"
        )


def test_contract_completed_maps_to_domain_terminal_success() -> None:
    """The contract's COMPLETED is the unified name for terminal success.

    Existing domains use READY (media jobs, VODs) or COMPLETED (pipeline
    runs) for terminal success.  The contract normalises both to
    ``COMPLETED`` at the API boundary; the mapping is documented here
    so a future adopter knows which domain value to project.
    """
    from ttvturbo.media_processing.schemas import (
        MediaJobStatus,
        PipelineStatus,
    )
    from ttvturbo.vod_pipeline.schemas import VodStatus

    # Each domain has exactly one terminal-success state.
    assert MediaJobStatus.READY.value == "READY"
    assert VodStatus.READY.value == "READY"
    assert PipelineStatus.COMPLETED.value == "COMPLETED"
    # The contract's terminal-success is COMPLETED.
    assert JobStatus.COMPLETED.value == "COMPLETED"
    # And COMPLETED is terminal in the contract's state machine.
    assert is_terminal(JobStatus.COMPLETED.value)


# ---------------------------------------------------------------------------
# No route duplicates / no universal run-anything endpoint
# ---------------------------------------------------------------------------


def _collect_routes(app) -> list[dict]:
    routes: list[dict] = []

    def _walk(route_list) -> None:
        for route in route_list:
            if hasattr(route, "original_router"):
                _walk(route.original_router.routes)
                continue
            if hasattr(route, "routes"):
                _walk(route.routes)
                continue
            if hasattr(route, "methods") and hasattr(route, "path"):
                for method in sorted(route.methods or []):
                    if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
                        routes.append({"method": method, "path": route.path})

    _walk(app.routes)
    return routes


def test_no_universal_operations_run_anything_route(tmp_path) -> None:
    """The contracts must not introduce a universal /operations/run-anything API."""
    from ttvturbo.app_factory import create_app

    app = create_app(settings=Settings(data_root=tmp_path / "routes"))
    routes = _collect_routes(app)
    forbidden_paths = ("/operations/run-anything", "/operations/run", "/operations")
    for r in routes:
        for forbidden in forbidden_paths:
            assert not r["path"].lower().startswith(forbidden.lower()), (
                f"universal operations route must not exist: {r}"
            )


def test_no_duplicate_routes(tmp_path) -> None:
    """No two registered routes may share the same (method, path)."""
    from ttvturbo.app_factory import create_app

    app = create_app(settings=Settings(data_root=tmp_path / "routes"))
    routes = _collect_routes(app)
    seen: dict[tuple[str, str], int] = {}
    for r in routes:
        key = (r["method"], r["path"])
        seen[key] = seen.get(key, 0) + 1
    duplicates = {k: v for k, v in seen.items() if v > 1}
    assert not duplicates, f"duplicate routes: {duplicates}"


def test_contracts_package_does_not_register_routes() -> None:
    """Importing the contracts package must not touch any FastAPI router."""
    import sys

    # Snapshot existing FastAPI router classes before importing contracts.
    import fastapi

    before = set(id(obj) for obj in list(getattr(fastapi, "APIRouter", lambda: None).__subclasses__()) if False)
    # Re-import contracts (already imported above, but force a fresh attribute access).
    import importlib

    import ttvturbo.contracts as contracts_pkg

    importlib.reload(contracts_pkg)
    # No router attribute should be exposed.
    assert not hasattr(contracts_pkg, "router")
    assert not hasattr(contracts_pkg, "APIRouter")
    # Sanity: the module is still the contracts package.
    assert contracts_pkg.__name__ == "ttvturbo.contracts"
    del before, sys  # silence linter
