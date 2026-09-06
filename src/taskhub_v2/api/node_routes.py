from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


@router.get("")
async def nodes(request: Request) -> dict:
    return {"nodes": await request.app.state.node_scheduler.status()}
