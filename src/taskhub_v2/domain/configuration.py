import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MODEL_FIELDS = (
    "provider",
    "openai_base_url",
    "openai_proxy_url",
    "openai_model",
    "gpt_base_url",
    "gpt_model",
    "gpt_planner_model",
    "gpt_coder_model",
    "gpt_supervisor_model",
    "deepseek_base_url",
    "deepseek_model",
    "minimax_base_url",
    "minimax_model",
)
MODEL_SECRET_FIELDS = (
    "openai_api_key",
    "gpt_api_key",
    "deepseek_api_key",
    "minimax_api_key",
)
PLATFORM_FIELDS = (
    "seed_public_url",
    "node_callback_url",
    "node_container_image",
    "node_image_registry",
    "node_image_proxy",
    "node_registry_username",
    "default_node_slots",
    "default_node_cpu_limit",
    "default_node_memory_limit",
    "node_heartbeat_seconds",
    "node_offline_seconds",
    "log_retention_days",
    "artifact_retention_days",
)
PLATFORM_SECRET_FIELDS = ("node_registry_password",)


class ModelServicesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["deterministic", "openai", "routed"] | None = None
    openai_base_url: str | None = Field(default=None, max_length=500)
    openai_proxy_url: str | None = Field(default=None, max_length=500)
    openai_model: str | None = Field(default=None, max_length=200)
    gpt_base_url: str | None = Field(default=None, max_length=500)
    gpt_model: str | None = Field(default=None, max_length=200)
    gpt_planner_model: str | None = Field(default=None, max_length=200)
    gpt_coder_model: str | None = Field(default=None, max_length=200)
    gpt_supervisor_model: str | None = Field(default=None, max_length=200)
    deepseek_base_url: str | None = Field(default=None, max_length=500)
    deepseek_model: str | None = Field(default=None, max_length=200)
    minimax_base_url: str | None = Field(default=None, max_length=500)
    minimax_model: str | None = Field(default=None, max_length=200)
    openai_api_key: str | None = Field(default=None, max_length=4096)
    gpt_api_key: str | None = Field(default=None, max_length=4096)
    deepseek_api_key: str | None = Field(default=None, max_length=4096)
    minimax_api_key: str | None = Field(default=None, max_length=4096)

    @field_validator("openai_base_url", "gpt_base_url", "deepseek_base_url", "minimax_base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return _validated_url(value, allow_empty=False)

    @field_validator("openai_proxy_url")
    @classmethod
    def validate_proxy_url(cls, value: str | None) -> str | None:
        return _validated_url(value, allow_empty=True)

    @field_validator(
        "openai_model",
        "gpt_model",
        "gpt_planner_model",
        "gpt_coder_model",
        "gpt_supervisor_model",
        "deepseek_model",
        "minimax_model",
    )
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9._:/-]+", value):
            raise ValueError("model name contains unsupported characters")
        return value

    @field_validator(*MODEL_SECRET_FIELDS)
    @classmethod
    def validate_secret(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(ord(character) < 32 for character in value):
            raise ValueError("API key contains control characters")
        return value.strip()


class PlatformSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_public_url: str | None = Field(default=None, max_length=500)
    node_callback_url: str | None = Field(default=None, max_length=500)
    node_container_image: str | None = Field(default=None, max_length=500)
    node_image_registry: str | None = Field(default=None, max_length=500)
    node_image_proxy: str | None = Field(default=None, max_length=500)
    node_registry_username: str | None = Field(default=None, max_length=200)
    node_registry_password: str | None = Field(default=None, max_length=4096)
    default_node_slots: int | None = Field(default=None, ge=1, le=64)
    default_node_cpu_limit: str | None = Field(default=None, max_length=32)
    default_node_memory_limit: str | None = Field(default=None, max_length=32)
    node_heartbeat_seconds: int | None = Field(default=None, ge=5, le=3600)
    node_offline_seconds: int | None = Field(default=None, ge=10, le=86400)
    log_retention_days: int | None = Field(default=None, ge=1, le=3650)
    artifact_retention_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("seed_public_url", "node_callback_url")
    @classmethod
    def validate_public_url(cls, value: str | None) -> str | None:
        return _validated_url(value, allow_empty=True)

    @field_validator("node_container_image")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value or not re.fullmatch(r"[A-Za-z0-9._:/@-]+", value):
            raise ValueError("invalid node image reference")
        return value

    @field_validator(
        "node_image_registry",
        "node_image_proxy",
        "node_registry_username",
        "default_node_cpu_limit",
        "default_node_memory_limit",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator("node_image_registry", "node_image_proxy")
    @classmethod
    def validate_registry_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().strip("/")
        if value and not re.fullmatch(r"[A-Za-z0-9._:-]+(?:/[A-Za-z0-9._-]+)*", value):
            raise ValueError("registry must be a Docker image prefix without URL scheme")
        return value

    @field_validator("node_registry_password")
    @classmethod
    def validate_registry_password(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(ord(character) < 32 for character in value):
            raise ValueError("registry password contains control characters")
        return value


class ProviderConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: Literal[
        "deterministic", "openai_api", "gpt_api", "deepseek_api", "minimax_api"
    ]


def _validated_url(value: str | None, *, allow_empty: bool) -> str | None:
    if value is None:
        return value
    value = value.strip()
    if not value and allow_empty:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use HTTP or HTTPS and include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL cannot contain credentials, query, or fragment")
    return value.rstrip("/")
