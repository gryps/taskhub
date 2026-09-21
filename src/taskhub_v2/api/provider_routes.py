from typing import Annotated

from fastapi import APIRouter, Depends, Request

from taskhub_v2.services.providers import ProviderCatalog

router = APIRouter(prefix="/api/providers", tags=["providers"])


def get_catalog(request: Request) -> ProviderCatalog:
    return request.app.state.provider_catalog


Catalog = Annotated[ProviderCatalog, Depends(get_catalog)]


@router.get("")
async def provider_status(catalog: Catalog) -> dict:
    return await catalog.status()


@router.get("/operations")
async def provider_operations(request: Request, project_id: str = "") -> dict:
    return await request.app.state.model_operations.report(project_id=project_id)
