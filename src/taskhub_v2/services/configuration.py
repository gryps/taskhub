from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx

from taskhub_v2.config import Settings
from taskhub_v2.domain.configuration import (
    MODEL_FIELDS,
    MODEL_SECRET_FIELDS,
    PLATFORM_FIELDS,
    PLATFORM_SECRET_FIELDS,
    ModelServicesUpdate,
    PlatformSettingsUpdate,
)
from taskhub_v2.security import SecretCipher, mask_secret

MODEL_SCOPE = "model_services"
PLATFORM_SCOPE = "platform"


class ConfigurationError(ValueError):
    pass


class ManagedConfigurationService:
    def __init__(self, settings: Settings, store, cipher: SecretCipher | None):
        self.settings = settings
        self.store = store
        self.cipher = cipher
        self._pending_applied: list[tuple[str, int]] = []

    async def apply(self) -> Settings:
        updates: dict[str, Any] = {}
        applied: list[tuple[str, int]] = []
        for scope, fields in ((MODEL_SCOPE, MODEL_FIELDS), (PLATFORM_SCOPE, PLATFORM_FIELDS)):
            record = await self.store.get(scope)
            if not record:
                continue
            updates.update({key: value for key, value in record.payload.items() if key in fields})
            if record.encrypted_secrets:
                if not self.cipher:
                    raise ConfigurationError(
                        "managed secrets exist but TASKHUB_CONFIG_ENCRYPTION_KEY is missing"
                    )
                updates.update(
                    {
                        key: self.cipher.decrypt(value)
                        for key, value in record.encrypted_secrets.items()
                    }
                )
            if record.applied_version != record.version:
                applied.append((scope, record.version))
        effective = Settings.model_validate({**self.settings.model_dump(), **updates})
        self.settings = effective
        self._pending_applied = applied
        return effective

    async def mark_applied(self) -> None:
        for scope, version in self._pending_applied:
            await self.store.mark_applied(scope, version)
            await self.store.add_audit(
                operator="system",
                scope=scope,
                action="apply",
                parameter_summary={"version": version},
                result="applied",
            )
        self._pending_applied = []

    async def model_services(self) -> dict[str, Any]:
        record = await self.store.get(MODEL_SCOPE)
        desired, secrets = await self._model_values(record)
        return {
            "desired": desired,
            "effective": {field: getattr(self.settings, field) for field in MODEL_FIELDS},
            "secrets": {
                field: {
                    "configured": bool(secrets[field]),
                    "mask": mask_secret(secrets[field]),
                    "source": (
                        "managed" if record and field in record.encrypted_secrets else "deployment"
                    ),
                }
                for field in MODEL_SECRET_FIELDS
            },
            "encryption_configured": self.cipher is not None,
            **_metadata(record),
        }

    async def update_model_services(
        self, payload: ModelServicesUpdate, operator: str = "admin"
    ) -> dict[str, Any]:
        record = await self.store.get(MODEL_SCOPE)
        desired, secrets = await self._model_values(record)
        changes = payload.model_dump(exclude_unset=True)
        replaced_secrets = []
        for field in MODEL_SECRET_FIELDS:
            value = changes.pop(field, None)
            if value:
                if not self.cipher:
                    raise ConfigurationError(
                        "configure TASKHUB_CONFIG_ENCRYPTION_KEY before saving API keys"
                    )
                secrets[field] = value
                replaced_secrets.append(field)
        desired.update({key: value for key, value in changes.items() if value is not None})
        self._validate_provider_activation(desired, secrets)
        encrypted = dict(record.encrypted_secrets) if record else {}
        if self.cipher:
            encrypted.update(
                {field: self.cipher.encrypt(secrets[field]) for field in replaced_secrets}
            )
        saved = await self.store.save(MODEL_SCOPE, desired, encrypted)
        await self.store.add_audit(
            operator=operator,
            scope=MODEL_SCOPE,
            action="update",
            parameter_summary={
                "changed_fields": sorted(changes),
                "replaced_secrets": sorted(replaced_secrets),
                "version": saved.version,
            },
            result="pending_restart",
        )
        return await self.model_services()

    async def platform_settings(self) -> dict[str, Any]:
        record = await self.store.get(PLATFORM_SCOPE)
        desired, secrets = await self._platform_values(record)
        return {
            "desired": desired,
            "effective": {field: getattr(self.settings, field) for field in PLATFORM_FIELDS},
            "secrets": {
                field: {
                    "configured": bool(secrets[field]),
                    "mask": mask_secret(secrets[field]),
                    "source": (
                        "managed" if record and field in record.encrypted_secrets else "deployment"
                    ),
                }
                for field in PLATFORM_SECRET_FIELDS
            },
            "protected_bootstrap": [
                "postgres_dsn",
                "session_secret",
                "config_encryption_key",
                "node_token",
            ],
            **_metadata(record),
        }

    async def update_platform_settings(
        self, payload: PlatformSettingsUpdate, operator: str = "admin"
    ) -> dict[str, Any]:
        record = await self.store.get(PLATFORM_SCOPE)
        desired, secrets = await self._platform_values(record)
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        replaced_secrets = []
        for field in PLATFORM_SECRET_FIELDS:
            value = changes.pop(field, None)
            if value:
                if not self.cipher:
                    raise ConfigurationError(
                        "configure TASKHUB_CONFIG_ENCRYPTION_KEY before saving registry credentials"
                    )
                secrets[field] = value
                replaced_secrets.append(field)
        desired.update(changes)
        if desired["node_offline_seconds"] <= desired["node_heartbeat_seconds"]:
            raise ConfigurationError("offline threshold must exceed heartbeat interval")
        if desired.get("node_registry_username") and not secrets["node_registry_password"]:
            raise ConfigurationError("private registry username requires a registry password")
        encrypted = dict(record.encrypted_secrets) if record else {}
        if self.cipher:
            encrypted.update(
                {field: self.cipher.encrypt(secrets[field]) for field in replaced_secrets}
            )
        saved = await self.store.save(PLATFORM_SCOPE, desired, encrypted)
        await self.store.add_audit(
            operator=operator,
            scope=PLATFORM_SCOPE,
            action="update",
            parameter_summary={
                "changed_fields": sorted(changes),
                "replaced_secrets": sorted(replaced_secrets),
                "version": saved.version,
            },
            result="pending_restart",
        )
        return await self.platform_settings()

    async def test_provider(self, provider_id: str, operator: str = "admin") -> dict[str, Any]:
        record = await self.store.get(MODEL_SCOPE)
        desired, secrets = await self._model_values(record)
        if provider_id == "deterministic":
            result = {"available": True, "detail": "内置确定性模型无需外部连接"}
        else:
            mapping = {
                "openai_api": ("openai_base_url", "openai_api_key", True),
                "gpt_api": ("gpt_base_url", "gpt_api_key", True),
                "deepseek_api": ("deepseek_base_url", "deepseek_api_key", False),
                "minimax_api": ("minimax_base_url", "minimax_api_key", False),
            }
            base_field, secret_field, use_proxy = mapping[provider_id]
            key = secrets[secret_field]
            if not key:
                result = {"available": False, "detail": "尚未配置 API Key"}
            else:
                try:
                    options: dict[str, Any] = {"timeout": 12, "trust_env": False}
                    if use_proxy:
                        if not desired["openai_proxy_url"]:
                            raise ConfigurationError("OpenAI 服务需要配置代理地址")
                        options["proxy"] = desired["openai_proxy_url"]
                    async with httpx.AsyncClient(**options) as client:
                        response = await client.get(
                            f"{desired[base_field].rstrip('/')}/models",
                            headers={"Authorization": f"Bearer {key}"},
                        )
                    result = {
                        "available": response.status_code < 400,
                        "detail": (
                            f"连接成功，服务返回 HTTP {response.status_code}"
                            if response.status_code < 400
                            else f"连接失败，服务返回 HTTP {response.status_code}"
                        ),
                    }
                except (httpx.HTTPError, ConfigurationError) as exc:
                    result = {"available": False, "detail": _safe_error(exc)}
        await self.store.add_audit(
            operator=operator,
            scope=MODEL_SCOPE,
            action="connection_test",
            parameter_summary={"provider_id": provider_id},
            result="passed" if result["available"] else "failed",
        )
        return {"provider_id": provider_id, **result}

    async def audit(self, scope: str | None = None, limit: int = 50) -> dict[str, Any]:
        items = await self.store.list_audit(scope, limit)
        return {"events": [_audit_view(item) for item in items]}

    async def _model_values(self, record) -> tuple[dict[str, Any], dict[str, str]]:
        desired = {field: getattr(self.settings, field) for field in MODEL_FIELDS}
        secrets = {field: getattr(self.settings, field) for field in MODEL_SECRET_FIELDS}
        if record:
            desired.update(record.payload)
            if record.encrypted_secrets:
                if not self.cipher:
                    raise ConfigurationError(
                        "managed secrets cannot be read without encryption key"
                    )
                secrets.update(
                    {
                        field: self.cipher.decrypt(value)
                        for field, value in record.encrypted_secrets.items()
                    }
                )
        return desired, secrets

    async def _platform_values(self, record) -> tuple[dict[str, Any], dict[str, str]]:
        desired = {field: getattr(self.settings, field) for field in PLATFORM_FIELDS}
        secrets = {field: getattr(self.settings, field) for field in PLATFORM_SECRET_FIELDS}
        if record:
            desired.update(record.payload)
            if record.encrypted_secrets:
                if not self.cipher:
                    raise ConfigurationError(
                        "managed secrets cannot be read without encryption key"
                    )
                secrets.update(
                    {
                        field: self.cipher.decrypt(value)
                        for field, value in record.encrypted_secrets.items()
                        if field in PLATFORM_SECRET_FIELDS
                    }
                )
        return desired, secrets

    @staticmethod
    def _validate_provider_activation(desired: dict[str, Any], secrets: dict[str, str]) -> None:
        if desired["provider"] == "deterministic":
            return
        if not desired["openai_proxy_url"]:
            raise ConfigurationError("OpenAI provider mode requires a proxy URL")
        if desired["provider"] == "openai":
            if not secrets["openai_api_key"] or not desired["openai_model"]:
                raise ConfigurationError("OpenAI mode requires API Key and model")
            return
        required = (
            ("gpt_api_key", "gpt_model"),
            ("deepseek_api_key", "deepseek_model"),
            ("minimax_api_key", "minimax_model"),
        )
        if any(not secrets[secret] or not desired[model] for secret, model in required):
            raise ConfigurationError("routed mode requires GPT, DeepSeek and MiniMax credentials")


def _metadata(record) -> dict[str, Any]:
    if not record:
        return {
            "version": 0,
            "applied_version": 0,
            "restart_required": False,
            "updated_at": None,
            "applied_at": None,
        }
    return {
        "version": record.version,
        "applied_version": record.applied_version,
        "restart_required": record.version != record.applied_version,
        "updated_at": record.updated_at.isoformat(),
        "applied_at": record.applied_at.isoformat() if record.applied_at else None,
    }


def _audit_view(item: dict[str, Any]) -> dict[str, Any]:
    created = item["created_at"]
    return {
        **item,
        "created_at": created.isoformat() if isinstance(created, datetime) else str(created),
    }


def _safe_error(error: Exception) -> str:
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", str(error))
    text = re.sub(r"[A-Za-z0-9_-]{24,}", "[redacted]", text)
    return f"连接失败：{type(error).__name__} · {text[:180]}"
