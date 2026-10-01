from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.domain.governance import EngineeringRule
from taskhub_v2.projects import ProjectNotFoundError
from taskhub_v2.services.engineering_governance import (
    EngineeringGovernanceError,
    EngineeringGovernanceService,
)
from taskhub_v2.services.project_contracts import ProjectContractNotFoundError

router = APIRouter(prefix="/api")


class PolicyDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(default="", max_length=500)
    rules: list[EngineeringRule] = Field(min_length=1)


class PolicyExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=10, max_length=4_000)
    risk: str = Field(min_length=10, max_length=4_000)
    controls: list[str] = Field(min_length=1)
    recovery_condition: str = Field(min_length=10, max_length=2_000)
    expires_at: datetime


def _service(request: Request) -> EngineeringGovernanceService:
    return request.app.state.engineering_governance


def _actor(request: Request) -> str:
    return str(request.state.session.get("actor") or "system")


def _raise(error: Exception):
    raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/engineering-policies")
async def engineering_policies(request: Request) -> dict[str, Any]:
    policies = await _service(request).list_policies()
    return {"policies": policies}


@router.get("/engineering-policies/active")
async def active_engineering_policy(request: Request):
    try:
        return await _service(request).active_policy()
    except EngineeringGovernanceError as error:
        _raise(error)


@router.post("/engineering-policies/drafts", status_code=201)
async def create_engineering_policy_draft(
    payload: PolicyDraftRequest, request: Request
):
    try:
        return await _service(request).create_draft(
            name=payload.name,
            rules=payload.rules,
            source_reference=payload.source_reference,
            actor=_actor(request),
        )
    except EngineeringGovernanceError as error:
        _raise(error)


@router.post("/engineering-policies/{version}/activate")
async def activate_engineering_policy(version: int, request: Request):
    try:
        return await _service(request).activate(version, _actor(request))
    except EngineeringGovernanceError as error:
        _raise(error)


@router.get("/projects/{project_id}/engineering-governance")
async def project_engineering_governance(project_id: str, request: Request):
    try:
        contract = await request.app.state.project_contracts.current(project_id)
        return await _service(request).project_view(project_id, contract)
    except ProjectContractNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/projects/{project_id}/policy-exceptions", status_code=201)
async def propose_policy_exception(
    project_id: str, payload: PolicyExceptionRequest, request: Request
):
    try:
        request.app.state.projects.get(project_id)
        return await _service(request).propose_exception(
            project_id,
            **payload.model_dump(),
            scope="project",
            scope_ids=[],
            actor=_actor(request),
        )
    except (EngineeringGovernanceError, ProjectNotFoundError) as error:
        _raise(error)


@router.post("/projects/{project_id}/policy-exceptions/{exception_id}/approve")
async def approve_policy_exception(
    project_id: str, exception_id: str, request: Request
):
    try:
        return await _service(request).decide_exception(
            project_id, exception_id, approve=True, actor=_actor(request)
        )
    except EngineeringGovernanceError as error:
        _raise(error)


@router.post("/projects/{project_id}/policy-exceptions/{exception_id}/reject")
async def reject_policy_exception(
    project_id: str, exception_id: str, request: Request
):
    try:
        return await _service(request).decide_exception(
            project_id, exception_id, approve=False, actor=_actor(request)
        )
    except EngineeringGovernanceError as error:
        _raise(error)
