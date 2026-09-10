from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

HOST_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,31}$"
ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USERNAME_PATTERN = r"^[a-z_][a-z0-9_-]{0,31}$"
FINGERPRINT_PATTERN = r"^SHA256:[A-Za-z0-9+/]{20,60}$"
HostRole = Literal["execution", "test", "preproduction"]
HostOperationalState = Literal["active", "draining", "maintenance", "disabled"]


class HostConnection(BaseModel):
    address: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(pattern=USERNAME_PATTERN)
    private_key: SecretStr
    docker_access: Literal["direct", "sudo"] = "direct"
    expected_fingerprint: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        value = value.strip()
        if not ADDRESS_PATTERN.fullmatch(value) or ".." in value:
            raise ValueError("请输入不含协议或路径的 IP 地址或主机名")
        return value.lower()


class PhysicalHostCreate(HostConnection):
    host_id: str = Field(pattern=HOST_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=100)
    allowed_roles: list[HostRole] = Field(
        default_factory=lambda: ["execution", "test", "preproduction"],
        min_length=1,
        max_length=3,
    )
    labels: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=1000)

    @field_validator("allowed_roles")
    @classmethod
    def unique_roles(cls, values: list[HostRole]) -> list[HostRole]:
        return list(dict.fromkeys(values))

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            value = value.strip()
            if not value or len(value) > 40 or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                raise ValueError("标签只能包含字母、数字、点、下划线和连字符")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned


class HostStateChange(BaseModel):
    state: HostOperationalState


class HostNodeRebuild(BaseModel):
    target_host_id: str = Field(pattern=HOST_ID_PATTERN)
    node_ids: list[str] = Field(default_factory=list, max_length=100)
