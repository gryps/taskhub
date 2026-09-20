from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from taskhub_v2.domain.configuration import (
    GitRepositoryConnectionTest,
    ModelCardConnectionDraft,
    ModelServicesUpdate,
    PlatformSettingsUpdate,
    ProviderConnectionTest,
)
from taskhub_v2.services.configuration import ConfigurationError, ManagedConfigurationService
from taskhub_v2.services.device_auth import DeviceAuthError

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_configuration(request: Request) -> ManagedConfigurationService:
    return request.app.state.managed_configuration


ConfigurationDep = Annotated[ManagedConfigurationService, Depends(get_configuration)]


class DeviceAuthStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    draft: ModelCardConnectionDraft | None = None

    @property
    def selected_draft(self) -> dict | None:
        if not self.draft or self.draft.model_id != self.model_id:
            return None
        return self.draft.model_dump(exclude={"api_key"})


@router.get("/model-services")
async def model_services(configuration: ConfigurationDep) -> dict:
    return await configuration.model_services()


@router.put("/model-services")
async def update_model_services(
    payload: ModelServicesUpdate, configuration: ConfigurationDep, request: Request
) -> dict:
    try:
        return await configuration.update_model_services(
            payload, operator=request.state.session["actor"]
        )
    except ConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/model-services/test")
async def test_model_service(
    payload: ProviderConnectionTest, configuration: ConfigurationDep, request: Request
) -> dict:
    return await configuration.test_provider(
        payload.provider_id,
        draft=payload.draft.model_dump() if payload.draft else None,
        operator=request.state.session["actor"],
    )


@router.post("/model-services/device-auth")
async def start_model_device_auth(
    payload: DeviceAuthStart, configuration: ConfigurationDep, request: Request
) -> dict:
    config = await configuration.model_services()
    card = next(
        (
            item
            for item in config["desired"].get("model_cards", [])
            if item["model_id"] == payload.model_id
        ),
        None,
    )
    card = payload.selected_draft or card
    if not card or card["auth_mode"] != "account":
        raise HTTPException(status_code=409, detail="当前卡片不是 ChatGPT 账号模式")
    try:
        return await request.app.state.device_auth.start(
            payload.model_id, proxy_url=card.get("proxy_url", "")
        )
    except DeviceAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/model-services/device-auth/{session_id}")
async def model_device_auth_status(session_id: str, request: Request) -> dict:
    try:
        return request.app.state.device_auth.status(session_id)
    except DeviceAuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/platform")
async def platform_settings(configuration: ConfigurationDep) -> dict:
    return await configuration.platform_settings()


@router.put("/platform")
async def update_platform_settings(
    payload: PlatformSettingsUpdate, configuration: ConfigurationDep, request: Request
) -> dict:
    try:
        return await configuration.update_platform_settings(
            payload, operator=request.state.session["actor"]
        )
    except ConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/platform/git-test")
async def test_git_repository(
    payload: GitRepositoryConnectionTest,
    configuration: ConfigurationDep,
    request: Request,
) -> dict:
    return await configuration.test_git_repository(
        payload, operator=request.state.session["actor"]
    )


@router.get("/audit")
async def configuration_audit(
    configuration: ConfigurationDep,
    scope: Literal["model_services", "platform", "physical_hosts"] | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    return await configuration.audit(scope, limit)
