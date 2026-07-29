"""FastAPI routes for non-destructive edit projects."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ttvturbo.api_utils import error_response
from ttvturbo.editing import (
    EditConflictError,
    EditNotFoundError,
    EditProjectService,
    EditStorageError,
    EditValidationError,
)

logger = logging.getLogger("ttvturbo.editing_api")


class SourceRequest(BaseModel):
    media_item_id: str
    asset_id: Optional[str] = None
    sha256: Optional[str] = None
    source_revision: Optional[str] = None


class SequenceSpec(BaseModel):
    id: Optional[str] = None
    name: str
    width: int
    height: int
    fps_numerator: int = 60
    fps_denominator: int = 1
    format_profile: str = "CUSTOM"


class CreateProjectRequest(BaseModel):
    name: str
    sources: list[SourceRequest]
    sequences: Optional[list[SequenceSpec]] = None
    author: Optional[str] = None


class CreateCommitRequest(BaseModel):
    branch_id: str
    expected_head_commit_id: str
    message: str = "Edit"
    author: Optional[str] = None
    operations: list[dict[str, Any]] = Field(min_length=1)


class CheckoutCommitRequest(BaseModel):
    commit_id: str


class RevertRequest(BaseModel):
    branch_id: str
    expected_head_commit_id: str
    commit_ids: list[str] = Field(min_length=1)
    message: Optional[str] = None
    author: Optional[str] = None


class CreateSequenceRequest(BaseModel):
    branch_id: str
    expected_head_commit_id: str
    sequence: SequenceSpec
    derive_from_sequence_id: Optional[str] = None
    message: Optional[str] = None


class UpdateSequenceRequest(BaseModel):
    branch_id: str
    expected_head_commit_id: str
    width: Optional[int] = None
    height: Optional[int] = None
    fps_numerator: Optional[int] = None
    fps_denominator: Optional[int] = None
    format_profile: Optional[str] = None
    message: Optional[str] = None


class CreateBranchRequest(BaseModel):
    name: str
    from_commit_id: Optional[str] = None


class RenameBranchRequest(BaseModel):
    name: str


class ResetBranchRequest(BaseModel):
    expected_head_commit_id: str
    target_commit_id: str
    confirmed: bool = False


class MergePreviewRequest(BaseModel):
    source_branch_id: str
    target_branch_id: str


class MergeFinalizeRequest(BaseModel):
    merge_id: str
    resolutions: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None
    author: Optional[str] = None


class ResolveMergeRequest(BaseModel):
    resolutions: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None
    author: Optional[str] = None


class RenderProjectionRequest(BaseModel):
    sequence_id: str
    commit_id: Optional[str] = None
    render_settings: dict[str, Any] = Field(default_factory=dict)


def _map_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, EditNotFoundError):
        return error_response(404, "edit_not_found", str(exc))
    if isinstance(exc, EditValidationError):
        return error_response(400, "edit_validation", str(exc))
    if isinstance(exc, EditConflictError):
        return error_response(409, "edit_conflict", str(exc))
    if isinstance(exc, EditStorageError):
        return error_response(500, "edit_storage", str(exc))
    logger.exception("unexpected edit-project error")
    return error_response(500, "edit_internal", "Internal edit-project error.")


def build_editing_router(service: EditProjectService) -> APIRouter:
    router = APIRouter(prefix="/api/edit-projects", tags=["edit-projects"])

    @router.post("")
    def create_project(req: CreateProjectRequest) -> JSONResponse:
        try:
            project = service.create_project(
                name=req.name,
                sources=[x.model_dump(exclude_none=True) for x in req.sources],
                sequences=[x.model_dump(exclude_none=True) for x in req.sequences] if req.sequences else None,
                author=req.author,
            )
            return JSONResponse(status_code=201, content=project)
        except Exception as exc:
            return _map_error(exc)

    @router.get("")
    def list_projects() -> JSONResponse:
        return JSONResponse(content={"projects": service.list_projects()})

    @router.get("/{project_id}")
    def get_project(project_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.get_project(project_id))
        except Exception as exc:
            return _map_error(exc)

    @router.delete("/{project_id}")
    def delete_project(project_id: str) -> JSONResponse:
        try:
            service.delete_project(project_id)
            return JSONResponse(content={"id": project_id, "deleted": True})
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/commits")
    def create_commit(project_id: str, req: CreateCommitRequest) -> JSONResponse:
        try:
            result = service.create_commit(project_id, branch_id=req.branch_id, expected_head_commit_id=req.expected_head_commit_id, message=req.message, operations=req.operations, author=req.author)
            return JSONResponse(status_code=201, content=result)
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/commits")
    def list_commits(project_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> JSONResponse:
        try:
            return JSONResponse(content=service.list_commits(project_id, limit=limit, offset=offset))
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/commits/{commit_id}")
    def get_commit(project_id: str, commit_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.get_commit(project_id, commit_id))
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/commits/{commit_id}/state")
    def get_commit_state(project_id: str, commit_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.get_commit(project_id, commit_id, include_state=True))
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/checkout")
    def checkout_commit(project_id: str, req: CheckoutCommitRequest) -> JSONResponse:
        try:
            return JSONResponse(content=service.checkout_commit(project_id, req.commit_id))
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/revert")
    def revert(project_id: str, req: RevertRequest) -> JSONResponse:
        try:
            return JSONResponse(status_code=201, content=service.revert_commits(project_id, branch_id=req.branch_id, expected_head_commit_id=req.expected_head_commit_id, commit_ids=req.commit_ids, message=req.message, author=req.author))
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/sequences")
    def list_sequences(project_id: str, commit_id: Optional[str] = None) -> JSONResponse:
        try:
            return JSONResponse(content={"sequences": service.list_sequences(project_id, commit_id=commit_id)})
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/sequences")
    def create_sequence(project_id: str, req: CreateSequenceRequest) -> JSONResponse:
        try:
            result = service.create_sequence(project_id, branch_id=req.branch_id, expected_head_commit_id=req.expected_head_commit_id, sequence=req.sequence.model_dump(exclude_none=True), derive_from_sequence_id=req.derive_from_sequence_id, message=req.message)
            return JSONResponse(status_code=201, content=result)
        except Exception as exc:
            return _map_error(exc)

    @router.patch("/{project_id}/sequences/{sequence_id}")
    def update_sequence(project_id: str, sequence_id: str, req: UpdateSequenceRequest) -> JSONResponse:
        try:
            updates = req.model_dump(exclude_none=True, exclude={"branch_id", "expected_head_commit_id", "message"})
            return JSONResponse(content=service.update_sequence(project_id, sequence_id, branch_id=req.branch_id, expected_head_commit_id=req.expected_head_commit_id, updates=updates, message=req.message))
        except Exception as exc:
            return _map_error(exc)


    @router.post("/{project_id}/sequences/{sequence_id}/checkout")
    def checkout_sequence(project_id: str, sequence_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.checkout_sequence(project_id, sequence_id))
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/source-integrity")
    def source_integrity(project_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.verify_sources(project_id))
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/branches")
    def list_branches(project_id: str) -> JSONResponse:
        try:
            return JSONResponse(content={"branches": service.list_branches(project_id)})
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/branches")
    def create_branch(project_id: str, req: CreateBranchRequest) -> JSONResponse:
        try:
            return JSONResponse(status_code=201, content=service.create_branch(project_id, name=req.name, from_commit_id=req.from_commit_id))
        except Exception as exc:
            return _map_error(exc)

    @router.patch("/{project_id}/branches/{branch_id}")
    def rename_branch(project_id: str, branch_id: str, req: RenameBranchRequest) -> JSONResponse:
        try:
            return JSONResponse(content=service.rename_branch(project_id, branch_id, req.name))
        except Exception as exc:
            return _map_error(exc)

    @router.delete("/{project_id}/branches/{branch_id}")
    def delete_branch(project_id: str, branch_id: str) -> JSONResponse:
        try:
            service.delete_branch(project_id, branch_id)
            return JSONResponse(content={"id": branch_id, "deleted": True})
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/branches/{branch_id}/checkout")
    def checkout_branch(project_id: str, branch_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.checkout_branch(project_id, branch_id))
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/branches/{branch_id}/reset")
    def reset_branch(project_id: str, branch_id: str, req: ResetBranchRequest) -> JSONResponse:
        try:
            return JSONResponse(content=service.reset_branch(project_id, branch_id, expected_head_commit_id=req.expected_head_commit_id, target_commit_id=req.target_commit_id, confirmed=req.confirmed))
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/merges/preview")
    def preview_merge(project_id: str, req: MergePreviewRequest) -> JSONResponse:
        try:
            return JSONResponse(status_code=201, content=service.preview_merge(project_id, source_branch_id=req.source_branch_id, target_branch_id=req.target_branch_id))
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/merges")
    def finalize_merge(project_id: str, req: MergeFinalizeRequest) -> JSONResponse:
        try:
            return JSONResponse(content=service.finalize_merge(project_id, req.merge_id, resolutions=req.resolutions, message=req.message, author=req.author))
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/merges/{merge_id}")
    def get_merge(project_id: str, merge_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.get_merge(project_id, merge_id))
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/merges/{merge_id}/resolve")
    def resolve_merge(project_id: str, merge_id: str, req: ResolveMergeRequest) -> JSONResponse:
        try:
            return JSONResponse(content=service.finalize_merge(project_id, merge_id, resolutions=req.resolutions, message=req.message, author=req.author))
        except Exception as exc:
            return _map_error(exc)


    @router.get("/{project_id}/compare")
    def compare_commits(project_id: str, from_commit_id: str, to_commit_id: str) -> JSONResponse:
        try:
            return JSONResponse(content=service.compare_commits(project_id, from_commit_id, to_commit_id))
        except Exception as exc:
            return _map_error(exc)

    @router.get("/{project_id}/history-graph")
    def history_graph(project_id: str, limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0)) -> JSONResponse:
        try:
            return JSONResponse(content=service.history_graph(project_id, limit=limit, offset=offset))
        except Exception as exc:
            return _map_error(exc)

    @router.post("/{project_id}/render-projections")
    def render_projection(project_id: str, req: RenderProjectionRequest) -> JSONResponse:
        try:
            return JSONResponse(status_code=201, content=service.render_projection(project_id, sequence_id=req.sequence_id, commit_id=req.commit_id, render_settings=req.render_settings))
        except Exception as exc:
            return _map_error(exc)

    return router
