from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


CONTRACT_EXAMPLE = """workload: browser_acceptance
required_capabilities: [windows_gui, playwright, screenshot, trace]
preview:
  command: [python3, -m, uvicorn, app:create_app, --factory, --host, 0.0.0.0, --port, "{port}"]
  health_path: /api/health
  stop_command: []
  timeout_seconds: 120
browsers: [chromium, edge]
command: [npx, playwright, test]
suite: tests/e2e/acceptance.yaml
timeout_seconds: 900
required_artifacts: [playwright-report, junit.xml, screenshots, trace.zip]
# health_path must return JSON containing git_commit equal to TASKHUB_GIT_COMMIT.
# On Windows, launch the installed browsers from TASKHUB_CHROMIUM_CHANNEL=chrome
# and TASKHUB_EDGE_CHANNEL=msedge; do not require Playwright-downloaded Chromium.
"""

SUITE_EXAMPLE = """scenarios:
  - id: task-list
    description: Create, refresh and filter tasks
    browsers: [chromium, edge]
"""


class AcceptanceContractValidationError(ValueError):
    reason = "acceptance_contract_invalid"

    def __init__(self, validation_error: Exception):
        self.detail = (
            ".taskhub/acceptance.yaml does not match the required schema. "
            "Use this exact structure and replace only project-specific commands/paths:\n"
            f"{CONTRACT_EXAMPLE}\nThe referenced suite must use this structure:\n"
            f"{SUITE_EXAMPLE}\nValidation details:\n{validation_error}"
        )
        super().__init__(self.detail)


class AcceptanceSuiteValidationError(ValueError):
    reason = "acceptance_suite_invalid"

    def __init__(self, validation_error: Exception):
        self.detail = (
            "The browser acceptance suite does not match the required schema. "
            "Each scenario permits only id, description and browsers; split multiple "
            "checks into separate scenarios when needed. Use this structure:\n"
            f"{SUITE_EXAMPLE}\nValidation details:\n{validation_error}"
        )
        super().__init__(self.detail)


class PreviewContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: list[str] = Field(min_length=1)
    health_path: str = "/api/health"
    stop_command: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=900)

    @model_validator(mode="after")
    def require_allocated_port(self):
        if not any("{port}" in part for part in self.command):
            raise ValueError("preview.command must contain the {port} placeholder")
        return self


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
    browsers: set[str] = Field(default_factory=lambda: {"chromium", "edge"}, min_length=1)


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
    try:
        contract = AcceptanceContract.model_validate(payload)
    except ValidationError as exc:
        raise AcceptanceContractValidationError(exc) from exc
    suite_path = (Path(repository) / contract.suite).resolve()
    root = Path(repository).resolve()
    if not suite_path.is_relative_to(root):
        raise ValueError("acceptance suite must stay inside the repository")
    if suite_path.is_file():
        suite_payload = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
        try:
            suite = AcceptanceSuite.model_validate(suite_payload)
        except ValidationError as exc:
            raise AcceptanceSuiteValidationError(exc) from exc
        unsupported = {
            browser for scenario in suite.scenarios for browser in scenario.browsers
        } - set(contract.browsers)
        if unsupported:
            raise ValueError("acceptance suite uses undeclared browsers: " + ", ".join(sorted(unsupported)))
    return contract


def load_acceptance_suite(repository: str | Path, contract: AcceptanceContract) -> AcceptanceSuite:
    path = (Path(repository) / contract.suite).resolve()
    if not path.is_relative_to(Path(repository).resolve()):
        raise ValueError("acceptance suite must stay inside the repository")
    if not path.is_file():
        raise ValueError(f"project does not define {contract.suite}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("browser acceptance suite must contain a mapping")
    try:
        return AcceptanceSuite.model_validate(payload)
    except ValidationError as exc:
        raise AcceptanceSuiteValidationError(exc) from exc
