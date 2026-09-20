import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    "model_cards",
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
    "provider_failure_threshold",
    "provider_recovery_threshold",
    "provider_probe_interval_seconds",
    "provider_switch_lock_seconds",
    "authority_git_host",
    "authority_git_port",
    "authority_git_root",
    "managed_repository_root",
)
PLATFORM_SECRET_FIELDS = ("node_registry_password", "authority_git_private_key")


ModelRole = Literal["planner", "coder", "supervisor", "reviewer", "risk"]


class ModelRoleAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ModelRole
    priority: int = Field(ge=0, le=9)


class ModelCardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    service_type: Literal["openai", "deepseek", "minimax", "custom"]
    auth_mode: Literal["api", "account"] = "api"
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    proxy_url: str = Field(default="", max_length=500)
    enabled: bool = True
    assignments: list[ModelRoleAssignment] = Field(default_factory=list, max_length=5)
    api_key: str | None = Field(default=None, max_length=4096)

    @field_validator("base_url", "proxy_url")
    @classmethod
    def validate_optional_url(cls, value: str) -> str:
        return _validated_url(value, allow_empty=True) or ""

    @field_validator("model")
    @classmethod
    def validate_card_model(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9._:/-]+", value):
            raise ValueError("model name contains unsupported characters")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_card_secret(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(character.isspace() for character in value):
            raise ValueError("API key cannot contain whitespace")
        return value.strip() or None

    @model_validator(mode="after")
    def validate_mode(self):
        if self.auth_mode == "account" and self.service_type != "openai":
            raise ValueError("ChatGPT account mode is available only for OpenAI")
        if self.auth_mode == "api" and (not self.base_url or not self.model):
            raise ValueError("API mode requires API address and model")
        if self.service_type != "openai" and any(
            item.role == "coder" for item in self.assignments
        ):
            raise ValueError("coding role currently requires an OpenAI or ChatGPT account model")
        if len({item.role for item in self.assignments}) != len(self.assignments):
            raise ValueError("a model can assign each role only once")
        return self


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
    model_cards: list[ModelCardUpdate] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def validate_card_routes(self):
        if self.model_cards is None:
            return self
        ids = [item.model_id for item in self.model_cards]
        if len(ids) != len(set(ids)):
            raise ValueError("model IDs must be unique")
        enabled = [item for item in self.model_cards if item.enabled]
        unsupported_coders = [
            item.display_name
            for item in enabled
            if any(role.role == "coder" for role in item.assignments)
            and item.service_type != "openai"
        ]
        if unsupported_coders:
            raise ValueError(
                f"coding role requires an OpenAI/Codex model: {', '.join(unsupported_coders)}"
            )
        for role in ("planner", "coder", "supervisor", "reviewer", "risk"):
            priorities = [
                assignment.priority
                for item in enabled
                for assignment in item.assignments
                if assignment.role == role
            ]
            if enabled and not priorities:
                raise ValueError(f"已启用模型缺少{role}主模型")
            if priorities and (priorities.count(0) != 1 or len(priorities) != len(set(priorities))):
                raise ValueError(f"{role}必须只有一个主模型，且备用顺序不能重复")
        return self

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
    provider_failure_threshold: int | None = Field(default=None, ge=1, le=20)
    provider_recovery_threshold: int | None = Field(default=None, ge=1, le=20)
    provider_probe_interval_seconds: int | None = Field(default=None, ge=10, le=3600)
    provider_switch_lock_seconds: int | None = Field(default=None, ge=30, le=86400)
    authority_git_host: str | None = Field(default=None, max_length=253)
    authority_git_port: int | None = Field(default=None, ge=1, le=65535)
    authority_git_root: str | None = Field(default=None, max_length=1000)
    managed_repository_root: str | None = Field(default=None, max_length=1000)
    authority_git_private_key: str | None = Field(default=None, max_length=32768)

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

    @field_validator("authority_git_host")
    @classmethod
    def validate_git_host(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not re.fullmatch(
            r"[A-Za-z0-9._-]+@(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+", value
        ):
            raise ValueError("Git host must use user@hostname format")
        return value

    @field_validator("authority_git_root", "managed_repository_root")
    @classmethod
    def validate_absolute_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().rstrip("/") or "/"
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("Git repository paths must be absolute")
        return value

    @field_validator("authority_git_private_key")
    @classmethod
    def validate_git_private_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if value and not re.fullmatch(
            r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----[\s\S]+"
            r"-----END (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----",
            value,
        ):
            raise ValueError("Git SSH private key format is invalid")
        return value or None


class GitRepositoryConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authority_git_host: str = Field(max_length=253)
    authority_git_port: int = Field(default=22, ge=1, le=65535)
    authority_git_root: str = Field(max_length=1000)
    managed_repository_root: str = Field(max_length=1000)
    authority_git_private_key: str | None = Field(default=None, max_length=32768)

    _host = field_validator("authority_git_host")(
        PlatformSettingsUpdate.validate_git_host.__func__
    )
    _paths = field_validator("authority_git_root", "managed_repository_root")(
        PlatformSettingsUpdate.validate_absolute_path.__func__
    )
    _key = field_validator("authority_git_private_key")(
        PlatformSettingsUpdate.validate_git_private_key.__func__
    )


class ModelCardConnectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    service_type: Literal["openai", "deepseek", "minimax", "custom"] = "custom"
    auth_mode: Literal["api", "account"] = "api"
    base_url: str = Field(default="", max_length=500)
    proxy_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, max_length=4096)

    @field_validator("base_url", "proxy_url")
    @classmethod
    def validate_optional_url(cls, value: str) -> str:
        return _validated_url(value, allow_empty=True) or ""

    @field_validator("api_key")
    @classmethod
    def validate_secret(cls, value: str | None) -> str | None:
        if value is not None and any(character.isspace() for character in value):
            raise ValueError("API key cannot contain whitespace")
        return value.strip() if value else None


class ProviderConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    draft: ModelCardConnectionDraft | None = None

    @model_validator(mode="after")
    def validate_draft_identity(self):
        if self.draft and self.draft.model_id != self.provider_id:
            raise ValueError("draft model ID must match provider ID")
        return self


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
