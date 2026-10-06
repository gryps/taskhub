from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.domain.project_contract import ContractCommands
from taskhub_v2.projects import ProjectNotFoundError
from taskhub_v2.services.command_lines import parse_command_lines
from taskhub_v2.services.project_contracts import (
    ProjectContractConflictError,
    ProjectContractNotFoundError,
)

router = APIRouter(prefix="/api/projects", tags=["project-activation"])


class ContractQualityCommandsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_commands: str = Field(min_length=1, max_length=4000)


@router.get("/{project_id}/activation")
async def project_activation(project_id: str, request: Request) -> dict:
    try:
        return await request.app.state.project_activation.status(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc


@router.put("/{project_id}/project-contracts/{contract_id}/versions/{version}/quality-commands")
async def update_contract_quality_commands(
    project_id: str,
    contract_id: str,
    version: int,
    payload: ContractQualityCommandsRequest,
    request: Request,
):
    try:
        commands = parse_command_lines(payload.quality_commands, label="质量命令")
        if not commands:
            raise ValueError("至少填写一条质量命令")
        contract = await request.app.state.project_contracts.current(project_id)
        if not contract or contract.contract_id != contract_id or contract.version != version:
            raise ProjectContractNotFoundError("项目合同不存在")
        updated_commands = ContractCommands(
            install=contract.commands.install,
            format=contract.commands.format,
            test=commands,
            acceptance=contract.commands.acceptance,
        )
        return await request.app.state.project_contracts.update_draft(
            project_id,
            contract_id,
            version,
            {"commands": updated_commands},
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except ProjectContractNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectContractConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
