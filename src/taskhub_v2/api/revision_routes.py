from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.services.revisions import RevisionConflictError, RevisionNotFoundError

router = APIRouter(prefix="/api/change-requests", tags=["change-requests"])


class ChangeRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=80)
    plan_id: str
    plan_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=4_000)
    source_event: str = Field(default="manual.change_requested", max_length=200)
    affected_task_ids: list[str] = Field(default_factory=list, max_length=500)
    changed_paths: list[str] = Field(default_factory=list, max_length=500)


def _actor(request: Request) -> str:
    return str(request.state.session.get("actor") or "system")


def _service(request: Request):
    service = getattr(request.app.state, "revisions", None)
    if service is None:
        raise HTTPException(status_code=409, detail="productized orchestration is disabled")
    return service


def _raise(error: Exception):
    status = 404 if isinstance(error, RevisionNotFoundError) else 409
    raise HTTPException(status_code=status, detail=str(error)) from error


@router.get("")
async def list_change_requests(request: Request, project_id: str = Query(min_length=1)):
    return {"change_requests": await _service(request).list(project_id)}


@router.post("", status_code=201)
async def create_change_request(payload: ChangeRequestCreate, request: Request):
    try:
        return await _service(request).propose(
            payload.project_id,
            payload.plan_id,
            payload.plan_version,
            payload.reason,
            actor=_actor(request),
            source_event=payload.source_event,
            affected_task_ids=payload.affected_task_ids,
            changed_paths=payload.changed_paths,
        )
    except (RevisionConflictError, RevisionNotFoundError) as error:
        _raise(error)


@router.post("/{request_id}/approve")
async def approve_change_request(
    request_id: str, request: Request, project_id: str = Query(min_length=1)
):
    try:
        return await _service(request).approve(project_id, request_id, _actor(request))
    except (RevisionConflictError, RevisionNotFoundError) as error:
        _raise(error)


@router.post("/{request_id}/apply")
async def apply_change_request(
    request_id: str, request: Request, project_id: str = Query(min_length=1)
):
    try:
        return await _service(request).apply(project_id, request_id, _actor(request))
    except (RevisionConflictError, RevisionNotFoundError) as error:
        _raise(error)


@router.post("/{request_id}/reject")
async def reject_change_request(
    request_id: str, request: Request, project_id: str = Query(min_length=1)
):
    try:
        return await _service(request).reject(project_id, request_id)
    except (RevisionConflictError, RevisionNotFoundError) as error:
        _raise(error)
