from fastapi import APIRouter, HTTPException, Request

from taskhub_v2.services.containers import (
    ContainerCreate,
    DockerConflictError,
    DockerUnavailableError,
)

router = APIRouter(prefix="/api/containers", tags=["containers"])


def manager(request: Request):
    return request.app.state.container_manager


@router.get("/status")
def container_status(request: Request) -> dict:
    try:
        return manager(request).status()
    except DockerUnavailableError as exc:
        return {"enabled": True, "available": False, "detail": str(exc)}


@router.get("")
def containers(request: Request) -> dict:
    try:
        return {"containers": manager(request).list()}
    except DockerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("", status_code=201)
def create_container(payload: ContainerCreate, request: Request) -> dict:
    try:
        return manager(request).create(payload)
    except DockerConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DockerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{node_id}/{action}")
def container_action(node_id: str, action: str, request: Request) -> dict:
    if action not in {"start", "stop", "remove"}:
        raise HTTPException(status_code=404, detail="unknown container action")
    try:
        return manager(request).action(node_id, action)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="container not found") from exc
    except DockerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
