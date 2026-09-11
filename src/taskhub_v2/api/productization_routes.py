from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.domain.production import RequirementAttachment
from taskhub_v2.projects import ProjectNotFoundError
from taskhub_v2.services.productization import (
    ProductizationConflictError,
    ProductizationNotFoundError,
    ProductizationService,
)

router = APIRouter(prefix="/api")


class RequirementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=80)
    original_text: str = Field(min_length=3, max_length=20_000)
    attachments: list[RequirementAttachment] = Field(default_factory=list, max_length=20)


class RequirementSupplementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=20_000)


class ProductSpecUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = Field(default=None, max_length=4_000)
    goals: list[str] | None = None
    personas: list[str] | None = None
    in_scope: list[str] | None = None
    out_of_scope: list[str] | None = None
    functional_requirements: list[str] | None = None
    non_functional_requirements: list[str] | None = None
    modules: list[str] | None = None
    interfaces: list[dict[str, Any]] | None = None
    data_entities: list[dict[str, Any]] | None = None
    security_requirements: list[str] | None = None
    delivery_requirements: list[str] | None = None
    acceptance_criteria: list[str] | None = None
    risks: list[str] | None = None
    assumptions: list[str] | None = None
    capability_pack_lock: list[str] | None = None


class ProductDecisionResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, str] = Field(min_length=1, max_length=10)


class ProductSpecRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=4_000)


def _service(request: Request) -> ProductizationService:
    return request.app.state.productization


def _actor(request: Request) -> str:
    return str(request.state.session.get("actor") or "system")


def _project(request: Request, project_id: str) -> None:
    try:
        request.app.state.projects.get(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail="project is not registered") from error


def _raise(error: Exception):
    if isinstance(error, ProductizationNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/productization/status")
async def productization_status(request: Request) -> dict[str, bool]:
    return {"enabled": bool(request.app.state.production_orchestration_enabled)}


@router.post("/requirements", status_code=201)
async def create_requirement(payload: RequirementCreateRequest, request: Request):
    _project(request, payload.project_id)
    try:
        return await _service(request).submit_requirement(
            project_id=payload.project_id,
            original_text=payload.original_text,
            attachments=payload.attachments,
            actor=_actor(request),
        )
    except (ProductizationConflictError, ProductizationNotFoundError) as error:
        _raise(error)


@router.get("/requirements")
async def list_requirements(request: Request, project_id: str = Query(min_length=1)):
    _project(request, project_id)
    return {"requirements": await _service(request).list_requirements(project_id)}


@router.post("/requirements/{requirement_id}/supplements")
async def append_requirement_supplement(
    requirement_id: str, payload: RequirementSupplementRequest, request: Request
):
    _project(request, payload.project_id)
    try:
        return await _service(request).append_supplement(
            payload.project_id, requirement_id, payload.text, _actor(request)
        )
    except (ProductizationConflictError, ProductizationNotFoundError) as error:
        _raise(error)


@router.get("/product-specs")
async def list_product_specs(request: Request, project_id: str = Query(min_length=1)):
    _project(request, project_id)
    return {"product_specs": await _service(request).list_specs(project_id)}


@router.get("/product-specs/current")
async def current_product_spec(request: Request, project_id: str = Query(min_length=1)):
    _project(request, project_id)
    spec = await _service(request).current_spec(project_id)
    if spec is None:
        return {"product_spec": None, "requirements": [], "decisions": []}
    return await _service(request).detail(project_id, spec.spec_id, spec.version)


@router.get("/product-specs/{spec_id}/versions")
async def list_product_spec_versions(
    spec_id: str, request: Request, project_id: str = Query(min_length=1)
):
    _project(request, project_id)
    specs = [
        item for item in await _service(request).list_specs(project_id) if item.spec_id == spec_id
    ]
    return {"product_specs": sorted(specs, key=lambda item: item.version, reverse=True)}


@router.get("/product-specs/{spec_id}/versions/{version}")
async def get_product_spec(
    spec_id: str, version: int, request: Request, project_id: str = Query(min_length=1)
):
    _project(request, project_id)
    try:
        return await _service(request).detail(project_id, spec_id, version)
    except ProductizationNotFoundError as error:
        _raise(error)


@router.put("/product-specs/{spec_id}/versions/{version}")
async def update_product_spec(
    spec_id: str,
    version: int,
    payload: ProductSpecUpdateRequest,
    request: Request,
    project_id: str = Query(min_length=1),
):
    _project(request, project_id)
    try:
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        return await _service(request).update_draft(project_id, spec_id, version, changes)
    except (ProductizationConflictError, ProductizationNotFoundError) as error:
        _raise(error)


@router.post("/product-specs/{spec_id}/versions/{version}/review")
async def review_product_spec(
    spec_id: str, version: int, request: Request, project_id: str = Query(min_length=1)
):
    _project(request, project_id)
    try:
        return await _service(request).submit_review(project_id, spec_id, version)
    except (ProductizationConflictError, ProductizationNotFoundError) as error:
        _raise(error)


@router.post("/product-specs/{spec_id}/versions/{version}/approve")
async def approve_product_spec(
    spec_id: str, version: int, request: Request, project_id: str = Query(min_length=1)
):
    _project(request, project_id)
    try:
        return await _service(request).approve(project_id, spec_id, version)
    except (ProductizationConflictError, ProductizationNotFoundError) as error:
        _raise(error)


@router.post("/product-specs/{spec_id}/versions/{version}/revisions", status_code=201)
async def create_product_spec_revision(
    spec_id: str,
    version: int,
    payload: ProductSpecRevisionRequest,
    request: Request,
    project_id: str = Query(min_length=1),
):
    _project(request, project_id)
    try:
        return await _service(request).create_revision(
            project_id, spec_id, version, payload.reason, _actor(request)
        )
    except (ProductizationConflictError, ProductizationNotFoundError) as error:
        _raise(error)


@router.get("/product-specs/{spec_id}/diff")
async def product_spec_diff(
    spec_id: str,
    request: Request,
    project_id: str = Query(min_length=1),
    from_version: int = Query(ge=1),
    to_version: int = Query(ge=1),
):
    _project(request, project_id)
    try:
        return {
            "changes": await _service(request).diff(project_id, spec_id, from_version, to_version)
        }
    except ProductizationNotFoundError as error:
        _raise(error)


@router.post("/projects/{project_id}/product-decisions/{decision_id}/resolve")
async def resolve_product_decision(
    project_id: str,
    decision_id: str,
    payload: ProductDecisionResolveRequest,
    request: Request,
):
    _project(request, project_id)
    try:
        return await _service(request).resolve_decision(
            project_id, decision_id, payload.answers, _actor(request)
        )
    except (ProductizationConflictError, ProductizationNotFoundError) as error:
        _raise(error)
