from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CONTRACT_EXAMPLE = """workload: browser_acceptance
target: preview
required_capabilities: [windows_gui, playwright, screenshot, trace]
preview:
  command: [python3, -m, uvicorn, app:create_app, --factory, --host, 0.0.0.0, --port, "{port}"]
  health_path: /api/health
  stop_command: []
  timeout_seconds: 120
browsers: [chromium, edge]
setup_commands:
  - [npm, --prefix, apps/web, ci, --ignore-scripts, --no-audit, --no-fund]
command: [npx, playwright, test]
suite: tests/e2e/acceptance.yaml
timeout_seconds: 900
required_artifacts: [playwright-report, junit.xml, screenshots, trace.zip]
# health_path must return JSON containing git_commit equal to TASKHUB_GIT_COMMIT.
# On Windows, launch the installed browsers from TASKHUB_CHROMIUM_CHANNEL=chrome
# and TASKHUB_EDGE_CHANNEL=msedge; do not require Playwright-downloaded Chromium.
"""

PREPRODUCTION_EXAMPLE = """target: preproduction
preproduction:
  prepare_command: [python3, deploy/preproduction.py]
  health_path: /api/health
  timeout_seconds: 300
  commit_field: git_commit
  environment_field: environment
  database_revision_field: database_revision
  expected_database_revision: 0016_listing_workflow_v2
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


class PreproductionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prepare_command: list[str] = Field(min_length=1)
    health_path: str = Field(default="/api/health", pattern=r"^/")
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    commit_field: str = Field(default="git_commit", min_length=1, max_length=100)
    environment_field: str = Field(default="environment", min_length=1, max_length=100)
    database_revision_field: str = Field(default="database_revision", min_length=1, max_length=100)
    expected_database_revision: str = Field(default="", max_length=200)


class AcceptanceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workload: str = "browser_acceptance"
    target: str = "preview"
    required_capabilities: set[str] = Field(
        default_factory=lambda: {"windows_gui", "playwright", "screenshot", "trace"}
    )
    preview: PreviewContract | None = None
    preproduction: PreproductionContract | None = None
    browsers: list[str] = Field(default_factory=lambda: ["chromium", "edge"], min_length=1)
    setup_commands: list[list[str]] = Field(default_factory=list, max_length=10)
    command: list[str] = Field(min_length=1)
    suite: str = "tests/e2e/acceptance.yaml"
    timeout_seconds: int = Field(default=900, ge=1, le=3600)
    required_artifacts: list[str] = Field(
        default_factory=lambda: ["playwright-report", "junit.xml", "screenshots", "trace.zip"]
    )
    contract_schema_version: str = ""
    contract_id: str = ""
    contract_version: int | None = None
    quality_commands: list[list[str]] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    health_path: str = "/health"

    @model_validator(mode="after")
    def enforce_browser_lane(self):
        if self.workload != "browser_acceptance":
            raise ValueError("acceptance workload must be browser_acceptance")
        unsupported = set(self.browsers) - {"chromium", "edge"}
        if unsupported:
            raise ValueError(f"unsupported browsers: {', '.join(sorted(unsupported))}")
        if self.target not in {"preview", "preproduction"}:
            raise ValueError("acceptance target must be preview or preproduction")
        if self.target == "preview" and self.preview is None:
            raise ValueError("preview target requires preview configuration")
        if self.target == "preproduction" and self.preproduction is None:
            raise ValueError(
                "preproduction target requires preproduction configuration:\n"
                f"{PREPRODUCTION_EXAMPLE}"
            )
        if any(not command or len(command) > 100 for command in self.setup_commands):
            raise ValueError("each setup command must contain 1 to 100 arguments")
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
            raise ValueError(
                "acceptance suite uses undeclared browsers: " + ", ".join(sorted(unsupported))
            )
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
