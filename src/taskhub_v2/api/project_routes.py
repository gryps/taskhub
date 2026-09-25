import asyncio
import shlex
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from taskhub_v2.domain.models import (
    ProjectDefinition,
    ProjectSchedulingPolicy,
    TestEnvironmentDefinition,
    WindowsTestSuiteDefinition,
)
from taskhub_v2.projects import ProjectConflictError, ProjectNotFoundError, ProjectProvisionError

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    remote_url: str = Field(min_length=1, max_length=1000)
    local_path: str = Field(min_length=1, max_length=1000)
    base_ref: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]{1,200}$")
    test_commands: str = Field(default="", max_length=4000)
    acceptance_commands: str = Field(default="", max_length=4000)
    test_database: bool = False
    profile_id: Literal[
        "fullstack-web", "backend-api", "frontend-spa", "python-service", "worker-service"
    ] = "python-service"


class ProjectAttachRequest(BaseModel):
    name: str = Field(default="", max_length=100)
    remote_url: str = Field(min_length=1, max_length=1000)
    local_path: str = Field(min_length=1, max_length=1000)
    base_ref: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]{1,200}$")
    test_commands: str = Field(default="", max_length=4000)
    acceptance_commands: str = Field(default="", max_length=4000)
    test_database: bool = False


class TestEnvironmentRequest(BaseModel):
    target_url: str = Field(max_length=500)
    edge_host: str = Field(default="", max_length=253)
    origin_host: str = Field(default="", max_length=253)
    expected_environment: str = Field(default="production", max_length=40)


class ProjectRepositoryRequest(BaseModel):
    remote_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    remote_url: str = Field(min_length=1, max_length=1000)
    base_ref: str = Field(pattern=r"^[A-Za-z0-9._/-]{1,200}$")


class ProjectQualityRequest(BaseModel):
    test_commands: str = Field(default="", max_length=4000)
    acceptance_commands: str = Field(default="", max_length=4000)
    test_database: bool = False
    test_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    windows_test_commands: str | None = Field(default=None, max_length=4000)
    windows_test_node_ids: str | None = Field(default=None, max_length=1500)
    windows_test_artifact_paths: str | None = Field(default=None, max_length=4000)
    windows_test_browser: bool | None = None


class ProjectSchedulingPolicyRequest(ProjectSchedulingPolicy):
    pass


def parse_test_commands(value: str) -> list[list[str]]:
    commands = []
    for line in value.splitlines():
        if line.strip():
            try:
                commands.append(shlex.split(line))
            except ValueError as exc:
                raise ValueError(f"测试命令格式错误：{exc}") from exc
    return commands


def parse_lines(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]


WINDOWS_BROWSER_CAPABILITIES = {
    "browser_authenticated",
    "browser_profile",
    "chromium",
    "edge",
    "playwright",
    "screenshot",
    "trace",
}


def windows_test_suite(payload: ProjectQualityRequest) -> WindowsTestSuiteDefinition | None:
    commands = parse_test_commands(payload.windows_test_commands or "")
    if not commands:
        return None
    node_ids = set(parse_lines(payload.windows_test_node_ids or ""))
    if not node_ids:
        raise ValueError(
            "启用 Windows 实机测试时，必须显式填写当前项目获授权的节点 ID"
        )
    capabilities = {"windows_gui"}
    if payload.windows_test_browser:
        capabilities.update(WINDOWS_BROWSER_CAPABILITIES)
    return WindowsTestSuiteDefinition(
        commands=commands,
        node_ids=node_ids,
        required_capabilities=capabilities,
        artifact_paths=parse_lines(payload.windows_test_artifact_paths or ""),
    )


def project_view(item: ProjectDefinition, registry=None) -> dict:
    repository_settings = registry.repository_settings(item) if registry else None
    return {
        "id": item.id,
        "name": item.name or item.id,
        "repository": item.repository,
        "base_ref": item.base_ref,
        "test_commands": item.test_commands,
        "acceptance_commands": item.acceptance_commands,
        "test_timeout_seconds": item.test_timeout_seconds,
        "test_database": "test_database" in item.acceptance_capabilities,
        "windows_test_suite": (
            {
                **item.windows_test_suite.model_dump(
                    mode="json", exclude={"node_ids", "required_capabilities"}
                ),
                "node_ids": sorted(item.windows_test_suite.node_ids),
                "required_capabilities": sorted(
                    item.windows_test_suite.required_capabilities
                ),
            }
            if item.windows_test_suite else None
        ),
        "test_environment": (
            item.test_environment.model_dump(mode="json") if item.test_environment else None
        ),
        "scheduling_policy": item.scheduling_policy.model_dump(mode="json"),
        "repository_ready": (repository_settings["ready"] if repository_settings else True),
        "repository_settings": repository_settings,
    }


@router.get("")
async def projects(request: Request) -> dict:
    registry = request.app.state.projects
    return {"projects": [project_view(item, registry) for item in registry.list()]}


@router.get("/available")
async def available_projects(request: Request) -> dict:
    provisioner = request.app.state.project_provisioner
    try:
        repositories = await provisioner.available()
    except (ProjectProvisionError, OSError) as exc:
        return {
            "repositories": [],
            "defaults": provisioner.defaults(),
            "discovery_error": str(exc),
        }
    return {
        "repositories": repositories,
        "defaults": provisioner.defaults(),
        "discovery_error": None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreateRequest, request: Request) -> dict:
    try:
        created = await request.app.state.project_provisioner.create(
            payload.name.strip(),
            payload.project_id,
            payload.remote_url,
            payload.local_path,
            payload.base_ref,
            parse_test_commands(payload.test_commands),
            parse_test_commands(payload.acceptance_commands),
            {"test_database"} if payload.test_database else set(),
        )
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectProvisionError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.app.state.production_orchestration_enabled:
        try:
            await request.app.state.project_contracts.create_draft(
                created.id,
                profile_id=payload.profile_id,
                inferred=False,
                actor=str(request.state.session.get("actor") or "system"),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"项目已创建，但合同生成失败：{exc}"
            ) from exc
    return project_view(created, request.app.state.projects)


@router.post("/attach", status_code=status.HTTP_201_CREATED)
async def attach_project(payload: ProjectAttachRequest, request: Request) -> dict:
    try:
        created = await request.app.state.project_provisioner.attach(
            payload.name,
            payload.remote_url,
            payload.local_path,
            payload.base_ref,
            parse_test_commands(payload.test_commands),
            parse_test_commands(payload.acceptance_commands),
            {"test_database"} if payload.test_database else set(),
        )
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectProvisionError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.app.state.production_orchestration_enabled:
        try:
            await request.app.state.project_contracts.create_draft(
                created.id,
                profile_id=None,
                inferred=True,
                actor=str(request.state.session.get("actor") or "system"),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"项目已接入，但合同扫描失败：{exc}"
            ) from exc
    return project_view(created, request.app.state.projects)


@router.put("/{project_id}/repository")
async def update_project_repository(
    project_id: str, payload: ProjectRepositoryRequest, request: Request
) -> dict:
    try:
        updated = await asyncio.to_thread(
            request.app.state.projects.configure_repository,
            project_id,
            remote_name=payload.remote_name,
            remote_url=payload.remote_url.strip(),
            base_ref=payload.base_ref,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project_view(updated, request.app.state.projects)


@router.post("/{project_id}/repository/check")
async def check_project_repository(project_id: str, request: Request) -> dict:
    try:
        project = request.app.state.projects.get(project_id)
        return await asyncio.to_thread(request.app.state.projects.check_repository, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{project_id}/quality")
async def update_project_quality(
    project_id: str, payload: ProjectQualityRequest, request: Request
) -> dict:
    try:
        project = request.app.state.projects.get(project_id)
        suite = (
            windows_test_suite(payload)
            if payload.windows_test_commands is not None
            else project.windows_test_suite
        )
        updated = request.app.state.projects.update(
            project.model_copy(
                update={
                    "test_commands": parse_test_commands(payload.test_commands),
                    "acceptance_commands": parse_test_commands(payload.acceptance_commands),
                    "acceptance_capabilities": (
                        {"test_database"} if payload.test_database else set()
                    ),
                    "test_timeout_seconds": (
                        payload.test_timeout_seconds
                        if payload.test_timeout_seconds is not None
                        else project.test_timeout_seconds
                    ),
                    "windows_test_suite": suite,
                }
            )
        )
        request.app.state.operation_log.record(
            "project_quality",
            "updated",
            actor=str(request.state.session.get("actor") or "system"),
            project_id=project_id,
            test_command_count=len(updated.test_commands),
            acceptance_command_count=len(updated.acceptance_commands),
            test_timeout_seconds=updated.test_timeout_seconds,
            test_database=payload.test_database,
            windows_test_suite=bool(updated.windows_test_suite),
        )
        return project_view(updated, request.app.state.projects)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{project_id}/preflight")
async def project_preflight(project_id: str, request: Request) -> dict:
    try:
        return await request.app.state.project_preflight.run(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc


@router.put("/{project_id}/scheduling-policy")
async def update_project_scheduling_policy(
    project_id: str, payload: ProjectSchedulingPolicyRequest, request: Request
) -> dict:
    try:
        project = request.app.state.projects.get(project_id)
        updated = request.app.state.projects.update(
            project.model_copy(
                update={"scheduling_policy": ProjectSchedulingPolicy(**payload.model_dump())}
            )
        )
        request.app.state.operation_log.record(
            "project_scheduling_policy",
            "updated",
            actor=str(request.state.session.get("actor") or "system"),
            project_id=project_id,
            policy=updated.scheduling_policy.model_dump(mode="json"),
        )
        return project_view(updated, request.app.state.projects)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc


@router.put("/{project_id}/test-environment")
async def update_test_environment(
    project_id: str, payload: TestEnvironmentRequest, request: Request
) -> dict:
    try:
        project = request.app.state.projects.get(project_id)
        environment = TestEnvironmentDefinition(**payload.model_dump())
        updated = request.app.state.projects.update(
            project.model_copy(update={"test_environment": environment})
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project_view(updated, request.app.state.projects)


@router.delete("/{project_id}/test-environment")
async def delete_test_environment(project_id: str, request: Request) -> dict:
    try:
        project = request.app.state.projects.get(project_id)
        updated = request.app.state.projects.update(
            project.model_copy(update={"test_environment": None})
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    return project_view(updated, request.app.state.projects)


@router.post("/{project_id}/test-environment/check")
async def check_test_environment(project_id: str, request: Request) -> dict:
    try:
        project = request.app.state.projects.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    if project.test_environment is None:
        raise HTTPException(status_code=409, detail="请先配置预生产环境")

    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(project.test_environment.target_url)
    except httpx.HTTPError as exc:
        return {
            "available": False,
            "detail": f"无法连接验收入口：{type(exc).__name__}",
        }
    return {
        "available": response.status_code < 500,
        "status_code": response.status_code,
        "detail": f"验收入口已响应 HTTP {response.status_code}",
    }
