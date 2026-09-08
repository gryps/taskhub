import shlex

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from taskhub_v2.domain.models import ProjectDefinition
from taskhub_v2.projects import ProjectConflictError, ProjectProvisionError

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    base_ref: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]{1,200}$")
    test_commands: str = Field(default="", max_length=4000)
    acceptance_commands: str = Field(default="", max_length=4000)
    test_database: bool = False


class ProjectAttachRequest(BaseModel):
    name: str = Field(default="", max_length=100)
    repository: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.git$")
    base_ref: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]{1,200}$")
    test_commands: str = Field(default="", max_length=4000)
    acceptance_commands: str = Field(default="", max_length=4000)
    test_database: bool = False


def parse_test_commands(value: str) -> list[list[str]]:
    commands = []
    for line in value.splitlines():
        if line.strip():
            try:
                commands.append(shlex.split(line))
            except ValueError as exc:
                raise ValueError(f"测试命令格式错误：{exc}") from exc
    return commands


def project_view(item: ProjectDefinition) -> dict:
    return {
        "id": item.id,
        "name": item.name or item.id,
        "repository": item.repository,
        "base_ref": item.base_ref,
        "test_commands": item.test_commands,
        "acceptance_commands": item.acceptance_commands,
        "test_database": "test_database" in item.acceptance_capabilities,
        "repository_ready": True,
    }


@router.get("")
async def projects(request: Request) -> dict:
    return {"projects": [project_view(item) for item in request.app.state.projects.list()]}


@router.get("/available")
async def available_projects(request: Request) -> dict:
    try:
        repositories = await request.app.state.project_provisioner.available()
    except (ProjectProvisionError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"repositories": repositories}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreateRequest, request: Request) -> dict:
    try:
        created = await request.app.state.project_provisioner.create(
            payload.name.strip(),
            payload.project_id,
            payload.base_ref,
            parse_test_commands(payload.test_commands),
            parse_test_commands(payload.acceptance_commands),
            {"test_database"} if payload.test_database else set(),
        )
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectProvisionError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project_view(created)


@router.post("/attach", status_code=status.HTTP_201_CREATED)
async def attach_project(payload: ProjectAttachRequest, request: Request) -> dict:
    try:
        created = await request.app.state.project_provisioner.attach(
            payload.repository,
            payload.name,
            payload.base_ref,
            parse_test_commands(payload.test_commands),
            parse_test_commands(payload.acceptance_commands),
            {"test_database"} if payload.test_database else set(),
        )
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectProvisionError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project_view(created)
