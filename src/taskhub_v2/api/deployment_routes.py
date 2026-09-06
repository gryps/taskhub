from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from taskhub_v2.api.dependencies import get_run_service
from taskhub_v2.deployment import DeploymentError
from taskhub_v2.domain.models import RunStatus
from taskhub_v2.projects import ProjectNotFoundError
from taskhub_v2.services import RunService
from taskhub_v2.services.runs import RunNotFoundError

router = APIRouter(prefix="/api/deployment", tags=["deployment"])
Service = Annotated[RunService, Depends(get_run_service)]


@router.get("/status")
async def deployment_status(run_id: str, request: Request, service: Service) -> dict:
    try:
        run = await service.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    current = request.app.state.deployment_manager.status()
    if run.publication and (
        current.get("project_id") != run.project_id
        or current.get("commit") != run.publication.published_commit
    ):
        current = {"status": "idle"}
    return {
        **current,
        "eligible": bool(
            run.status == RunStatus.COMPLETED
            and run.publication
            and request.app.state.deployment_manager.eligible(run.project_id)
        ),
    }


@router.post("/{run_id}", status_code=status.HTTP_202_ACCEPTED)
async def start_deployment(run_id: str, request: Request, service: Service) -> dict:
    try:
        run = await service.get(run_id)
        if run.status != RunStatus.COMPLETED or not run.publication:
            raise DeploymentError("任务尚未完成权威发布")
        project = request.app.state.projects.get(run.project_id)
        return await request.app.state.deployment_manager.start(project, run.publication)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project is not registered") from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
