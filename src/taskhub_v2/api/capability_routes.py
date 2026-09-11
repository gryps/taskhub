from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.services.capabilities import CapabilityConflictError, CapabilityNotFoundError

router = APIRouter(prefix="/api", tags=["capabilities"])


class PackImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]


class PackTrustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable_confirmed: bool = False


class PackAvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    reason: str = Field(default="", max_length=1_000)


class PackLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_id: str
    spec_version: int = Field(ge=1)
    pack_refs: list[str] = Field(min_length=1, max_length=20)


def _service(request: Request):
    return request.app.state.capability_packs


def _actor(request: Request) -> str:
    return str(request.state.session.get("actor") or "system")


def _raise(error: Exception):
    status = 404 if isinstance(error, CapabilityNotFoundError) else 409
    raise HTTPException(status_code=status, detail=str(error)) from error


@router.get("/capability-packs")
async def list_capability_packs(request: Request):
    return {"capability_packs": await _service(request).inventory()}


@router.post("/capability-packs/import", status_code=201)
async def import_capability_pack(payload: PackImportRequest, request: Request):
    try:
        return await _service(request).import_manifest(payload.manifest, _actor(request))
    except (CapabilityConflictError, CapabilityNotFoundError, ValueError) as error:
        _raise(error)


@router.post("/capability-packs/{pack_id}/versions/{version}/trust")
async def trust_capability_pack(
    pack_id: str, version: str, payload: PackTrustRequest, request: Request
):
    try:
        return await _service(request).trust(
            pack_id,
            version,
            _actor(request),
            executable_confirmed=payload.executable_confirmed,
        )
    except (CapabilityConflictError, CapabilityNotFoundError) as error:
        _raise(error)


@router.post("/capability-packs/{pack_id}/versions/{version}/availability")
async def set_capability_pack_availability(
    pack_id: str, version: str, payload: PackAvailabilityRequest, request: Request
):
    try:
        return await _service(request).set_enabled(
            pack_id, version, payload.enabled, payload.reason
        )
    except (CapabilityConflictError, CapabilityNotFoundError) as error:
        _raise(error)


@router.get("/projects/{project_id}/capability-recommendations")
async def recommend_capability_packs(
    project_id: str,
    request: Request,
    spec_id: str = Query(min_length=1),
    spec_version: int = Query(ge=1),
):
    try:
        return {
            "recommendations": await _service(request).recommendations(
                project_id, spec_id, spec_version
            )
        }
    except (CapabilityConflictError, CapabilityNotFoundError) as error:
        _raise(error)


@router.get("/projects/{project_id}/capability-lock")
async def current_capability_lock(project_id: str, request: Request):
    try:
        return await _service(request).current(project_id)
    except (CapabilityConflictError, CapabilityNotFoundError) as error:
        _raise(error)


@router.post("/projects/{project_id}/capability-locks", status_code=201)
async def create_capability_lock(project_id: str, payload: PackLockRequest, request: Request):
    try:
        return await _service(request).create_lock(
            project_id,
            payload.spec_id,
            payload.spec_version,
            payload.pack_refs,
            _actor(request),
        )
    except (CapabilityConflictError, CapabilityNotFoundError) as error:
        _raise(error)


@router.post("/projects/{project_id}/capability-locks/{version}/activate")
async def activate_capability_lock(project_id: str, version: int, request: Request):
    try:
        lock, contract = await _service(request).activate_lock(project_id, version, _actor(request))
        return {"capability_lock": lock, "design_contract": contract}
    except (CapabilityConflictError, CapabilityNotFoundError) as error:
        _raise(error)
