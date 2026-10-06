import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class WindowsNodeConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=1, max_length=253)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
    private_key: SecretStr
    expected_fingerprint: str | None = Field(
        default=None, pattern=r"^SHA256:[A-Za-z0-9+/]{20,60}$"
    )

    @field_validator("address")
    @classmethod
    def valid_address(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value):
            raise ValueError("请输入不含协议、端口或路径的 IP 地址或主机名")
        return value.lower()


class WindowsNodeInstall(WindowsNodeConnection):
    node_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=100)
    agent_port: int = Field(default=8301, ge=1024, le=65535)
    slots: int = Field(default=1, ge=1, le=16)
    browser_mode: bool = False
    browser_auth_target: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def browser_target_required(self):
        if self.browser_mode and not self.browser_auth_target.startswith(("http://", "https://")):
            raise ValueError("浏览器验收节点必须填写有效的登录目标地址")
        return self
