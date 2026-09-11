from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RemoteNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    host_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    role: Literal["execution", "test", "preproduction"]
    slots: int = Field(default=1, ge=1, le=16)
    host_port: int = Field(ge=1024, le=65535)
    cpu_limit: str = Field(default="", max_length=32)
    memory_limit: str = Field(default="", max_length=32)

    @field_validator("cpu_limit")
    @classmethod
    def valid_cpu(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                if not 0.1 <= float(value) <= 256:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("CPU 限额必须是 0.1 到 256 之间的数值") from exc
        return value

    @field_validator("memory_limit")
    @classmethod
    def valid_memory(cls, value: str) -> str:
        import re

        value = value.strip().lower()
        if value and not re.fullmatch(r"[1-9][0-9]*(?:[kmgt]i?b?|b)?", value):
            raise ValueError("内存限额格式无效，例如 4g 或 4096m")
        return value


class RemoteNodeRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remove_volume: bool = False


class RemoteNodeUpgrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(
        min_length=3,
        max_length=512,
        pattern=r"^[A-Za-z0-9._:/@-]+$",
    )
