from fastapi import APIRouter, HTTPException, Request

from taskhub_v2.domain.external_windows import WindowsNodeConnection, WindowsNodeInstall
from taskhub_v2.services.external_windows_nodes import ExternalWindowsNodeError

router = APIRouter(prefix="/api/external-windows-nodes", tags=["external-windows-nodes"])


@router.get("")
async def list_nodes(request: Request) -> dict:
    return await request.app.state.external_windows_nodes.list()


@router.post("/probe")
async def probe_node(payload: WindowsNodeConnection, request: Request) -> dict:
    try:
        return await request.app.state.external_windows_nodes.probe(payload)
    except ExternalWindowsNodeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("", status_code=201)
async def install_node(payload: WindowsNodeInstall, request: Request) -> dict:
    try:
        return await request.app.state.external_windows_nodes.install(payload)
    except ExternalWindowsNodeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/{node_id}")
async def remove_node(node_id: str, request: Request) -> dict:
    try:
        return await request.app.state.external_windows_nodes.remove(node_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="外部 Windows 节点不存在") from exc
