import asyncio
import re
import shlex
from uuid import uuid4

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


class ProjectAttachRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repository: str = Field(min_length=1, max_length=500)
    base_ref: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]{1,200}$")
    test_commands: str = Field(default="", max_length=4000)


def attached_project_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
    return slug if len(slug) >= 2 else f"project-{uuid4().hex[:8]}"


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
        "repository_ready": True,
    }


@router.get("")
async def projects(request: Request) -> dict:
    return {"projects": [project_view(item) for item in request.app.state.projects.list()]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreateRequest, request: Request) -> dict:
    try:
        created = await request.app.state.project_provisioner.create(
            payload.name.strip(),
            payload.project_id,
            payload.base_ref,
            parse_test_commands(payload.test_commands),
        )
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectProvisionError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project_view(created)


@router.post("/attach", status_code=status.HTTP_201_CREATED)
async def attach_project(payload: ProjectAttachRequest, request: Request) -> dict:
    try:
        project = ProjectDefinition(
            id=attached_project_id(payload.name),
            name=payload.name.strip(),
            repository=payload.repository.strip(),
            base_ref=payload.base_ref,
            test_commands=parse_test_commands(payload.test_commands),
        )
        created = await asyncio.to_thread(request.app.state.projects.add, project)
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project_view(created)
