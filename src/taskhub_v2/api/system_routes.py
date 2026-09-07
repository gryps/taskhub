from typing import Annotated

from fastapi import APIRouter, Depends, Request

from taskhub_v2.config import Settings
from taskhub_v2.services.diagnostics import controller_diagnostics, summarize_checks

router = APIRouter(prefix="/api/system", tags=["system"])


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/config")
async def system_config(request: Request, settings: SettingsDep) -> dict:
    controller = controller_diagnostics(settings)
    nodes = await request.app.state.node_scheduler.status()
    return {
        "status": summarize_checks(controller["checks"]),
        "controller": controller,
        "nodes": nodes,
    }
