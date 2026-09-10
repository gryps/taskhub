from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from taskhub_v2.domain.hosts import (
    HostConnection,
    HostNodeRebuild,
    HostStateChange,
    PhysicalHostCreate,
)
from taskhub_v2.services.hosts import HostAdmissionError, PhysicalHostService

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


def get_host_service(request: Request) -> PhysicalHostService:
    return request.app.state.physical_hosts


HostServiceDep = Annotated[PhysicalHostService, Depends(get_host_service)]


@router.get("")
async def list_hosts(service: HostServiceDep) -> dict:
    return await service.list()


@router.post("/probe")
async def probe_host(payload: HostConnection, service: HostServiceDep) -> dict:
    try:
        result = await service.probe(payload)
        result.pop("host_key", None)
        return result
    except HostAdmissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("", status_code=201)
async def admit_host(payload: PhysicalHostCreate, service: HostServiceDep) -> dict:
    try:
        return await service.save(payload)
    except HostAdmissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{host_id}/check")
async def check_host(host_id: str, service: HostServiceDep) -> dict:
    try:
        return await service.check(host_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="物理主机不存在") from exc
    except HostAdmissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{host_id}/state")
async def change_host_state(
    host_id: str, payload: HostStateChange, request: Request, service: HostServiceDep
) -> dict:
    try:
        result = await service.set_operational_state(host_id, payload.state)
        if payload.state == "active" and result["status"] == "available":
            affected = await request.app.state.remote_nodes.resume_host(host_id)
        else:
            affected = await request.app.state.remote_nodes.suspend_host(
                host_id, result["status_reason"]
            )
        return {**result, "affected_nodes": affected}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="物理主机不存在") from exc
    except (ValueError, HostAdmissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{host_id}/rebuild-nodes")
async def rebuild_host_nodes(host_id: str, payload: HostNodeRebuild, request: Request) -> dict:
    try:
        return await request.app.state.remote_nodes.rebuild_host_nodes(
            host_id, payload.target_host_id, payload.node_ids
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="物理主机不存在") from exc
    except (ValueError, HostAdmissionError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
