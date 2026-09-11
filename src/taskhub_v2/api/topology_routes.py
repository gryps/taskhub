from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.domain.topology import TopologyEdge, TopologyNode, TopologyViewport
from taskhub_v2.services.topologies import TopologyConflictError, TopologyNotFoundError

router = APIRouter(prefix="/api", tags=["production-topologies"])


class TopologyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
    viewport: TopologyViewport
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def _actor(request: Request) -> str:
    return str(request.state.session.get("actor") or "system")


def _raise(error: Exception):
    status = 404 if isinstance(error, TopologyNotFoundError) else 409
    raise HTTPException(status_code=status, detail=str(error)) from error


@router.get("/projects/{project_id}/topologies")
async def list_topologies(project_id: str, request: Request):
    return {"topologies": await request.app.state.topologies.list(project_id)}


@router.get("/projects/{project_id}/topologies/current")
async def current_topology(project_id: str, request: Request):
    return {"topology": await request.app.state.topologies.current(project_id)}


@router.post("/projects/{project_id}/topologies/draft", status_code=201)
async def create_topology_draft(project_id: str, request: Request):
    try:
        return await request.app.state.topologies.create_draft(project_id, _actor(request))
    except (LookupError, TopologyConflictError) as error:
        _raise(error)


@router.put("/projects/{project_id}/topologies/{topology_id}/versions/{version}")
async def update_topology(
    project_id: str,
    topology_id: str,
    version: int,
    payload: TopologyUpdateRequest,
    request: Request,
):
    try:
        return await request.app.state.topologies.update(
            project_id,
            topology_id,
            version,
            nodes=payload.nodes,
            edges=payload.edges,
            viewport=payload.viewport,
            expected_digest=payload.expected_digest,
        )
    except (TopologyNotFoundError, TopologyConflictError) as error:
        _raise(error)


@router.post("/projects/{project_id}/topologies/{topology_id}/versions/{version}/validate")
async def validate_topology(
    project_id: str, topology_id: str, version: int, request: Request
):
    try:
        return await request.app.state.topologies.validate(project_id, topology_id, version)
    except (TopologyNotFoundError, TopologyConflictError) as error:
        _raise(error)


@router.post("/projects/{project_id}/topologies/{topology_id}/versions/{version}/activate")
async def activate_topology(
    project_id: str, topology_id: str, version: int, request: Request
):
    try:
        return await request.app.state.topologies.activate(
            project_id, topology_id, version, _actor(request)
        )
    except (TopologyNotFoundError, TopologyConflictError) as error:
        _raise(error)


@router.get("/projects/{project_id}/topology-runtime")
async def topology_runtime(project_id: str, request: Request):
    return await request.app.state.topologies.runtime(
        project_id,
        request.app.state.run_service,
        request.app.state.dag_runtime,
    )
