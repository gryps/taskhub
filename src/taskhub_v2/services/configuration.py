from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from taskhub_v2.config import Settings
from taskhub_v2.domain.configuration import (
    MODEL_FIELDS,
    MODEL_SECRET_FIELDS,
    PLATFORM_FIELDS,
    PLATFORM_SECRET_FIELDS,
    GitRepositoryConnectionTest,
    ModelServicesUpdate,
    PlatformSettingsUpdate,
)
from taskhub_v2.security import SecretCipher, mask_secret
from taskhub_v2.services.configuration_views import audit_view, metadata, safe_error
from taskhub_v2.services.git_authority import test_git_repository_connection
from taskhub_v2.services.model_catalog import test_model_card

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
            scoped = {key: value for key, value in record.payload.items() if key in fields}
            updates.update(scoped)
            if record.encrypted_secrets:
                if not self.cipher:
                    raise ConfigurationError(
                        "managed secrets exist but TASKHUB_CONFIG_ENCRYPTION_KEY is missing"
                    )
                decrypted = {
                    key: self.cipher.decrypt(value)
                    for key, value in record.encrypted_secrets.items()
                }
                secret_fields = (
                    MODEL_SECRET_FIELDS if scope == MODEL_SCOPE else PLATFORM_SECRET_FIELDS
                )
                updates.update(
                    {key: value for key, value in decrypted.items() if key in secret_fields}
                )
                if scope == MODEL_SCOPE and scoped.get("model_cards"):
                    updates["model_cards"] = [
                        {
                            **card,
                            "api_key": decrypted.get(f"model_card__{card['model_id']}", ""),
                        }
                        for card in scoped["model_cards"]
                    ]
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
            "effective": {
                field: (
                    [{key: value for key, value in card.items() if key != "api_key"}
                     for card in getattr(self.settings, field)]
                    if field == "model_cards" else getattr(self.settings, field)
                )
                for field in MODEL_FIELDS
            },
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
            "card_credentials": {
                card["model_id"]: self._card_credential_state(card, secrets)
                for card in desired.get("model_cards", [])
            },
            "encryption_configured": self.cipher is not None,
            **metadata(record),
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
        cards = changes.pop("model_cards", None)
        encrypted = dict(record.encrypted_secrets) if record else {}
        if cards is not None:
            cleaned_cards = []
            retained = {f"model_card__{item['model_id']}" for item in cards}
            encrypted = {
                key: value
                for key, value in encrypted.items()
                if not key.startswith("model_card__") or key in retained
            }
            for card in cards:
                api_key = card.pop("api_key", None)
                secret_field = f"model_card__{card['model_id']}"
                if api_key:
                    if not self.cipher:
                        raise ConfigurationError(
                            "configure TASKHUB_CONFIG_ENCRYPTION_KEY before saving API keys"
                        )
                    encrypted[secret_field] = self.cipher.encrypt(api_key)
                    replaced_secrets.append(secret_field)
                if card["auth_mode"] == "api" and secret_field not in encrypted:
                    raise ConfigurationError(f"{card['display_name']} requires an API Key")
                cleaned_cards.append(card)
            desired["model_cards"] = cleaned_cards
            desired["provider"] = "routed" if cleaned_cards else "deterministic"
        desired.update({key: value for key, value in changes.items() if value is not None})
        self._validate_provider_activation(desired, secrets)
        if self.cipher:
            encrypted.update(
                {
                    field: self.cipher.encrypt(secrets[field])
                    for field in replaced_secrets
                    if field in secrets
                }
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
            **metadata(record),
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
                        "configure TASKHUB_CONFIG_ENCRYPTION_KEY before saving credentials"
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
    async def test_git_repository(
        self, payload: GitRepositoryConnectionTest, operator: str = "admin"
    ) -> dict[str, Any]:
        record = await self.store.get(PLATFORM_SCOPE)
        _, secrets = await self._platform_values(record)
        return await test_git_repository_connection(
            self.settings, self.store, payload,
            secrets.get("authority_git_private_key", ""), operator,
        )
    async def test_provider(
        self, provider_id: str, operator: str = "admin", draft: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        record = await self.store.get(MODEL_SCOPE)
        desired, secrets = await self._model_values(record)
        saved_card = next(
            (item for item in desired.get("model_cards", []) if item["model_id"] == provider_id),
            None,
        )
        card = draft or saved_card
        if draft and draft.get("api_key"):
            secrets[f"model_card__{provider_id}"] = draft.pop("api_key")
        if card:
            result = await self._test_model_card(card, secrets)
        elif provider_id == "deterministic":
            result = {"available": True, "detail": "内置确定性模型无需外部连接"}
        elif provider_id in {
            "openai_api",
            "gpt_api",
            "deepseek_api",
            "minimax_api",
        }:
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
                    result = {"available": False, "detail": safe_error(exc)}
        else:
            result = {
                "available": False,
                "detail": "模型卡片尚未保存，请先保存配置后再测试连接",
                "models": [],
            }
        await self.store.add_audit(
            operator=operator,
            scope=MODEL_SCOPE,
            action="connection_test",
            parameter_summary={"provider_id": provider_id},
            result="passed" if result["available"] else "failed",
        )
        return {"provider_id": provider_id, **result}

    async def _test_model_card(
        self, card: dict[str, Any], secrets: dict[str, str]
    ) -> dict[str, Any]:
        return await test_model_card(self.settings, card, secrets)

    def _card_credential_state(
        self, card: dict[str, Any], secrets: dict[str, str]
    ) -> dict[str, Any]:
        if card["auth_mode"] == "account":
            auth_file = Path(self.settings.model_account_root) / card["model_id"] / "auth.json"
            return {"configured": auth_file.is_file(), "kind": "account"}
        value = secrets.get(f"model_card__{card['model_id']}", "")
        return {"configured": bool(value), "kind": "api", "mask": mask_secret(value)}

    async def audit(self, scope: str | None = None, limit: int = 50) -> dict[str, Any]:
        items = await self.store.list_audit(scope, limit)
        return {"events": [audit_view(item) for item in items]}

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
        if desired.get("model_cards") is not None:
            return
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
