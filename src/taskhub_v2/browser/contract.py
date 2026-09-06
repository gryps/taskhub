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
    suite: str = "tests/e2e/acceptance.yaml"
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


class AcceptanceScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    description: str = Field(min_length=1)
    browsers: set[str] = Field(default_factory=lambda: {"chromium", "edge"})


class AcceptanceSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: list[AcceptanceScenario] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_scenarios(self):
        identifiers = [scenario.id for scenario in self.scenarios]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("acceptance scenario IDs must be unique")
        return self


def load_acceptance_contract(repository: str | Path) -> AcceptanceContract:
    path = Path(repository) / ".taskhub" / "acceptance.yaml"
    if not path.is_file():
        raise ValueError("project does not define .taskhub/acceptance.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("acceptance.yaml must contain a mapping")
    contract = AcceptanceContract.model_validate(payload)
    suite_path = (Path(repository) / contract.suite).resolve()
    root = Path(repository).resolve()
    if not suite_path.is_relative_to(root):
        raise ValueError("acceptance suite must stay inside the repository")
    if suite_path.is_file():
        suite_payload = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
        suite = AcceptanceSuite.model_validate(suite_payload)
        unsupported = {
            browser for scenario in suite.scenarios for browser in scenario.browsers
        } - set(contract.browsers)
        if unsupported:
            raise ValueError("acceptance suite uses undeclared browsers: " + ", ".join(sorted(unsupported)))
    return contract


def load_acceptance_suite(repository: str | Path, contract: AcceptanceContract) -> AcceptanceSuite:
    path = Path(repository) / contract.suite
    if not path.is_file():
        raise ValueError(f"project does not define {contract.suite}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("browser acceptance suite must contain a mapping")
    return AcceptanceSuite.model_validate(payload)
