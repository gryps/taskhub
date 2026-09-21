from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from taskhub_v2.services.containers import DockerUnavailableError

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/nodes/{node_id}")
async def node_diagnostics(node_id: str, request: Request, tail: int = 200) -> dict:
    try:
        return await request.app.state.system_diagnostics.node(node_id, tail)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="工作节点不存在") from exc
    except DockerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/export")
async def export_diagnostics(request: Request) -> Response:
    content, digest = await request.app.state.system_diagnostics.export()
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=taskhub-diagnostics.zip",
            "X-TaskHub-SHA256": digest,
        },
    )
