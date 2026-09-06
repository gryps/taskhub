from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreviewContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: list[str] = Field(min_length=1)
    health_path: str = "/api/health"
    stop_command: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=900)


class AcceptanceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workload: str = "browser_acceptance"
    required_capabilities: set[str] = Field(
        default_factory=lambda: {"windows_gui", "playwright", "screenshot", "trace"}
    )
    preview: PreviewContract
    browsers: list[str] = Field(default_factory=lambda: ["chromium", "edge"], min_length=1)
    command: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=900, ge=1, le=3600)
    required_artifacts: list[str] = Field(
        default_factory=lambda: ["playwright-report", "junit.xml", "screenshots", "trace.zip"]
    )

    @model_validator(mode="after")
    def enforce_browser_lane(self):
        if self.workload != "browser_acceptance":
            raise ValueError("acceptance workload must be browser_acceptance")
        unsupported = set(self.browsers) - {"chromium", "edge"}
        if unsupported:
            raise ValueError(f"unsupported browsers: {', '.join(sorted(unsupported))}")
        self.required_capabilities.update(self.browsers)
        return self


def load_acceptance_contract(repository: str | Path) -> AcceptanceContract:
    path = Path(repository) / ".taskhub" / "acceptance.yaml"
    if not path.is_file():
        raise ValueError("project does not define .taskhub/acceptance.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("acceptance.yaml must contain a mapping")
    return AcceptanceContract.model_validate(payload)
