import asyncio
import json
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from taskhub_v2.api.dependencies import get_run_service
from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.browser import load_acceptance_contract
from taskhub_v2.domain.models import (
    AcceptanceSubmission,
    ApprovalRequest,
    RebindProjectRequest,
    ResumeRequest,
    RunStatus,
    RunView,
    Stage,
    StartRunRequest,
    TaskPage,
    TaskSummary,
)
from taskhub_v2.execution.runner import NodeExecutionError
from taskhub_v2.projects import ProjectNotFoundError
from taskhub_v2.services.runs import RunConflictError, RunNotFoundError, RunService

router = APIRouter(prefix="/api")
Service = Annotated[RunService, Depends(get_run_service)]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "orchestrator": "langgraph"}


@router.post("/runs", response_model=RunView, status_code=201)
async def start_run(payload: StartRunRequest, request: Request, service: Service) -> RunView:
    try:
        if request.app.state.settings.worker_mode != "git":
            return await service.start(payload)
        project = request.app.state.projects.get(payload.project_id)
        try:
            await asyncio.to_thread(request.app.state.projects.check_repository, project)
        except (ValueError, OSError) as exc:
            raise RunConflictError(f"项目代码仓库未就绪：{exc}") from exc
        if project.test_environment:
            try:
                async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                    response = await client.get(project.test_environment.target_url)
                if response.status_code >= 500:
                    raise RunConflictError(
                        f"预生产环境未就绪：HTTP {response.status_code}"
                    )
            except httpx.HTTPError as exc:
                raise RunConflictError(
                    f"预生产环境未就绪：{type(exc).__name__}"
                ) from exc
        health = await request.app.state.node_scheduler.status()
        for capability in project.acceptance_capabilities:
            ready = any(
                item.get("status") == "ok"
                and "acceptance" in item.get("workloads", [])
                and item.get("capabilities", {}).get(capability)
                for item in health
            )
            if not ready:
                raise RunConflictError(f"验收前置配置未就绪：{capability}")
        contract_path = Path(project.repository) / ".taskhub" / "acceptance.yaml"
        if contract_path.is_file():
            contract = load_acceptance_contract(project.repository)
            await request.app.state.node_scheduler.preflight_browser(
                [contract.command], contract.required_capabilities
            )
        return await service.start(payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project is not registered") from exc
    except (NodeExecutionError, RunConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs", response_model=TaskPage)
async def list_runs(
    service: Service,
    project_id: str | None = None,
    production_line: str | None = None,
    status: RunStatus | None = None,
    stage: Stage | None = None,
    page: int = 1,
    page_size: int = 50,
    include_archived: bool = False,
) -> TaskPage:
    if page < 1 or not 1 <= page_size <= 200:
        raise HTTPException(status_code=422, detail="invalid pagination")
    return await service.list(project_id=project_id, production_line=production_line,
                              status=status, stage=stage, page=page, page_size=page_size,
                              include_archived=include_archived)


@router.post("/runs/{run_id}/archive", response_model=TaskSummary)
async def archive_run(run_id: str, service: Service):
    try:
        return await service.archive(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/rebind", response_model=RunView)
async def rebind_run(run_id: str, payload: RebindProjectRequest, service: Service):
    try:
        return await service.rebind(run_id, payload.project_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, service: Service) -> RunView:
    try:
        return await service.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/approval", response_model=RunView)
async def approve_run(run_id: str, payload: ApprovalRequest, service: Service) -> RunView:
    try:
        return await service.approve(run_id, payload)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/resume", response_model=RunView)
async def resume_run(run_id: str, payload: ResumeRequest, service: Service) -> RunView:
    try:
        return await service.resume(run_id, payload)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/replay", response_model=RunView)
async def replay_run(run_id: str, service: Service) -> RunView:
    try:
        return await service.replay(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/acceptance", response_model=RunView)
async def submit_acceptance(
    run_id: str, payload: AcceptanceSubmission, service: Service
) -> RunView:
    try:
        return await service.submit_acceptance(run_id, payload)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}/history")
async def run_history(run_id: str, service: Service) -> list[dict]:
    try:
        return await service.history(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, service: Service) -> StreamingResponse:
    async def stream():
        try:
            async for view in service.watch(run_id):
                yield f"event: run\ndata: {json.dumps(view.model_dump(mode='json'))}\n\n"
        except RunNotFoundError:
            yield 'event: error\ndata: {"detail":"run not found"}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/runs/{run_id}/artifacts/{name}")
async def download_run_artifact(run_id: str, name: str, request: Request) -> FileResponse:
    try:
        path = ArtifactStore(request.app.state.settings.artifact_root).path(run_id, name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    return FileResponse(path, filename=name)
