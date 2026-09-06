import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from taskhub_v2.api.dependencies import get_run_service
from taskhub_v2.domain.models import (
    AcceptanceSubmission,
    ApprovalRequest,
    ResumeRequest,
    RebindProjectRequest,
    RunStatus,
    RunView,
    Stage,
    StartRunRequest,
    TaskPage,
    TaskSummary,
)
from taskhub_v2.projects import ProjectNotFoundError
from taskhub_v2.services.runs import RunConflictError, RunNotFoundError, RunService

router = APIRouter(prefix="/api")
Service = Annotated[RunService, Depends(get_run_service)]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "orchestrator": "langgraph"}


@router.post("/runs", response_model=RunView, status_code=201)
async def start_run(payload: StartRunRequest, service: Service) -> RunView:
    try:
        return await service.start(payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project is not registered") from exc


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
