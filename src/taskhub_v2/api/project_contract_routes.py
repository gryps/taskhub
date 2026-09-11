from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.domain.project_contract import (
    ArtifactContract,
    ContractCommands,
    InterfaceContract,
    ManualReviewRule,
    MigrationContract,
    ModuleContract,
    RepositoryPolicy,
)
from taskhub_v2.services.project_contracts import (
    ProjectContractConflictError,
    ProjectContractNotFoundError,
    ProjectContractService,
)

router = APIRouter(prefix="/api")


class ContractDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: (
        Literal["fullstack-web", "backend-api", "frontend-spa", "python-service", "worker-service"]
        | None
    ) = None
    inferred: bool = True
    force_revision: bool = False


class ContractUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    languages: list[str] | None = None
    frameworks: list[str] | None = None
    directory_structure: list[str] | None = None
    modules: list[ModuleContract] | None = None
    interfaces: list[InterfaceContract] | None = None
    commands: ContractCommands | None = None
    migrations: MigrationContract | None = None
    artifacts: ArtifactContract | None = None
    repository_policy: RepositoryPolicy | None = None
    documentation_files: list[str] | None = None
    environment_example: str | None = Field(default=None, max_length=500)
    manual_review: list[ManualReviewRule] | None = None
    manual_evidence: dict[str, str] | None = None


class ContractGateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execute_commands: bool = True
    strict: bool = True
    manual_evidence: dict[str, str] = Field(default_factory=dict, max_length=30)


def _service(request: Request) -> ProjectContractService:
    return request.app.state.project_contracts


def _actor(request: Request) -> str:
    return str(request.state.session.get("actor") or "system")


def _raise(error: Exception):
    status = 404 if isinstance(error, ProjectContractNotFoundError) else 409
    raise HTTPException(status_code=status, detail=str(error)) from error


@router.get("/project-contract-profiles")
async def project_contract_profiles(request: Request) -> dict[str, list[dict]]:
    return {"profiles": _service(request).profiles()}


@router.get("/projects/{project_id}/project-contract")
async def current_project_contract(project_id: str, request: Request) -> dict[str, Any]:
    try:
        contract = await _service(request).current(project_id)
        return {"project_contract": contract}
    except ProjectContractNotFoundError as error:
        _raise(error)


@router.get("/projects/{project_id}/project-contracts")
async def list_project_contracts(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"project_contracts": await _service(request).list(project_id)}
    except ProjectContractNotFoundError as error:
        _raise(error)


@router.post("/projects/{project_id}/project-contracts/draft", status_code=201)
async def create_project_contract_draft(
    project_id: str, payload: ContractDraftRequest, request: Request
):
    try:
        return await _service(request).create_draft(
            project_id,
            profile_id=payload.profile_id,
            inferred=payload.inferred,
            actor=_actor(request),
            force_revision=payload.force_revision,
        )
    except (ProjectContractConflictError, ProjectContractNotFoundError) as error:
        _raise(error)


@router.put("/projects/{project_id}/project-contracts/{contract_id}/versions/{version}")
async def update_project_contract(
    project_id: str,
    contract_id: str,
    version: int,
    payload: ContractUpdateRequest,
    request: Request,
):
    try:
        return await _service(request).update_draft(
            project_id,
            contract_id,
            version,
            payload.model_dump(exclude_unset=True, exclude_none=True),
        )
    except (ProjectContractConflictError, ProjectContractNotFoundError) as error:
        _raise(error)


@router.post("/projects/{project_id}/project-contracts/{contract_id}/versions/{version}/review")
async def review_project_contract(
    project_id: str, contract_id: str, version: int, request: Request
):
    try:
        return await _service(request).submit_review(project_id, contract_id, version)
    except (ProjectContractConflictError, ProjectContractNotFoundError) as error:
        _raise(error)


@router.post("/projects/{project_id}/project-contracts/{contract_id}/versions/{version}/activate")
async def activate_project_contract(
    project_id: str, contract_id: str, version: int, request: Request
):
    try:
        return await _service(request).activate(project_id, contract_id, version, _actor(request))
    except (ProjectContractConflictError, ProjectContractNotFoundError) as error:
        _raise(error)


@router.get("/projects/{project_id}/project-contracts/{contract_id}/versions/{version}/documents")
async def project_contract_documents(
    project_id: str, contract_id: str, version: int, request: Request
):
    try:
        return {"documents": await _service(request).documents(project_id, contract_id, version)}
    except ProjectContractNotFoundError as error:
        _raise(error)


@router.post("/projects/{project_id}/project-contract/gate")
async def run_project_contract_gate(
    project_id: str, payload: ContractGateRequest, request: Request
):
    try:
        return await _service(request).gate(
            project_id,
            execute_commands=payload.execute_commands,
            strict=payload.strict,
            manual_evidence=payload.manual_evidence,
        )
    except (ProjectContractConflictError, ProjectContractNotFoundError, ValueError) as error:
        _raise(error)
