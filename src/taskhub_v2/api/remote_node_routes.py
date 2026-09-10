from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from taskhub_v2.domain.remote_nodes import RemoteNodeCreate, RemoteNodeRemove
from taskhub_v2.services.containers import DockerConflictError
from taskhub_v2.services.hosts import HostAdmissionError
from taskhub_v2.services.remote_nodes import RemoteNodeError, RemoteNodeService

router = APIRouter(prefix="/api/remote-nodes", tags=["remote-nodes"])


def get_service(request: Request) -> RemoteNodeService:
    return request.app.state.remote_nodes


ServiceDep = Annotated[RemoteNodeService, Depends(get_service)]


@router.get("")
async def list_remote_nodes(service: ServiceDep) -> dict:
    return await service.list()


@router.post("", status_code=202)
async def create_remote_node(payload: RemoteNodeCreate, service: ServiceDep) -> dict:
    try:
        return await service.create(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="物理主机不存在") from exc
    except DockerConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (HostAdmissionError, RemoteNodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{node_id}/{action}")
async def remote_node_action(
    node_id: str,
    action: Literal["start", "stop", "restart", "remove"],
    service: ServiceDep,
    payload: RemoteNodeRemove | None = None,
) -> dict:
    try:
        return await service.action(
            node_id, action, remove_volume=bool(payload and payload.remove_volume)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="远程节点不存在") from exc
    except (HostAdmissionError, RemoteNodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{node_id}/credential/rotate")
async def rotate_remote_node_credential(node_id: str, service: ServiceDep) -> dict:
    try:
        return await service.rotate_credential(node_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="远程节点不存在") from exc
    except (HostAdmissionError, RemoteNodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{node_id}/credential/revoke")
async def revoke_remote_node_credential(node_id: str, service: ServiceDep) -> dict:
    try:
        return await service.revoke_credential(node_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="远程节点不存在") from exc
    except (HostAdmissionError, RemoteNodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
