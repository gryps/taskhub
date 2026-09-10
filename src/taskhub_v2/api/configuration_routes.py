from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from taskhub_v2.domain.configuration import (
    ModelServicesUpdate,
    PlatformSettingsUpdate,
    ProviderConnectionTest,
)
from taskhub_v2.services.configuration import ConfigurationError, ManagedConfigurationService

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_configuration(request: Request) -> ManagedConfigurationService:
    return request.app.state.managed_configuration


ConfigurationDep = Annotated[ManagedConfigurationService, Depends(get_configuration)]


@router.get("/model-services")
async def model_services(configuration: ConfigurationDep) -> dict:
    return await configuration.model_services()


@router.put("/model-services")
async def update_model_services(
    payload: ModelServicesUpdate, configuration: ConfigurationDep
) -> dict:
    try:
        return await configuration.update_model_services(payload)
    except ConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/model-services/test")
async def test_model_service(
    payload: ProviderConnectionTest, configuration: ConfigurationDep
) -> dict:
    return await configuration.test_provider(payload.provider_id)


@router.get("/platform")
async def platform_settings(configuration: ConfigurationDep) -> dict:
    return await configuration.platform_settings()


@router.put("/platform")
async def update_platform_settings(
    payload: PlatformSettingsUpdate, configuration: ConfigurationDep
) -> dict:
    try:
        return await configuration.update_platform_settings(payload)
    except ConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/audit")
async def configuration_audit(
    configuration: ConfigurationDep,
    scope: Literal["model_services", "platform", "physical_hosts"] | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    return await configuration.audit(scope, limit)
