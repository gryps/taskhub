from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taskhub_v2.security.agent_access import AgentAccessError
from taskhub_v2.security.rbac import ROLE_PERMISSIONS

router = APIRouter(prefix="/api/auth", tags=["agent authentication"])


class PairingStartRequest(BaseModel):
    label: str = Field(min_length=2, max_length=80)


class PairingExchangeRequest(BaseModel):
    device_secret: str = Field(min_length=32, max_length=256)


class PairingApprovalRequest(BaseModel):
    role: Literal["project_owner", "developer", "auditor"] = "project_owner"
    expires_days: int = Field(default=30, ge=1, le=90)


@router.post("/agent-pairings/start")
async def start_pairing(payload: PairingStartRequest, request: Request) -> dict:
    identity = request.client.host if request.client else "unknown"
    try:
        result = request.app.state.agent_access.begin_pairing(payload.label, identity)
    except AgentAccessError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    request.app.state.operation_log.record(
        "agent_pairing", "started", label=payload.label, identity=identity
    )
    return result


@router.post("/agent-pairings/{pairing_id}/exchange")
async def exchange_pairing(
    pairing_id: str, payload: PairingExchangeRequest, request: Request
) -> dict:
    try:
        return request.app.state.agent_access.exchange_pairing(
            pairing_id, payload.device_secret
        )
    except AgentAccessError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error


@router.get("/agent-pairings")
async def pairings(request: Request) -> dict:
    return {"pairings": request.app.state.agent_access.pending_pairings()}


@router.post("/agent-pairings/{pairing_id}/approve")
async def approve_pairing(
    pairing_id: str, payload: PairingApprovalRequest, request: Request
) -> dict:
    session = request.state.session
    approver_permissions = ROLE_PERMISSIONS.get(str(session.get("role")), set())
    requested_permissions = ROLE_PERMISSIONS[payload.role]
    if "*" not in approver_permissions and not requested_permissions <= approver_permissions:
        raise HTTPException(status_code=403, detail="cannot grant permissions you do not hold")
    try:
        result = request.app.state.agent_access.approve_pairing(
            pairing_id,
            owner=str(session.get("actor")),
            role=payload.role,
            expires_days=payload.expires_days,
        )
    except AgentAccessError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    request.app.state.operation_log.record(
        "agent_pairing",
        "approved",
        actor=session.get("actor"),
        role=payload.role,
        pairing_id=pairing_id,
        credential_id=result.get("credential_id"),
    )
    return result


@router.post("/agent-pairings/{pairing_id}/reject")
async def reject_pairing(pairing_id: str, request: Request) -> dict:
    actor = str(request.state.session.get("actor"))
    try:
        request.app.state.agent_access.reject_pairing(pairing_id, actor)
    except AgentAccessError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    request.app.state.operation_log.record(
        "agent_pairing", "rejected", actor=actor, pairing_id=pairing_id
    )
    return {"rejected": pairing_id}


@router.get("/agent-credentials")
async def credentials(request: Request) -> dict:
    return {"credentials": request.app.state.agent_access.list_credentials()}


@router.delete("/agent-credentials/{credential_id}")
async def revoke_credential(credential_id: str, request: Request) -> dict:
    actor = str(request.state.session.get("actor"))
    try:
        request.app.state.agent_access.revoke_credential(credential_id, actor)
    except AgentAccessError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    request.app.state.operation_log.record(
        "agent_credential", "revoked", actor=actor, credential_id=credential_id
    )
    return {"revoked": credential_id}
