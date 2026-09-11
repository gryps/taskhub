from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taskhub_v2.domain.production_base import ProductionRecord


class ProjectContractStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ModuleContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    paths: list[str] = Field(min_length=1)
    may_import: list[str] = Field(default_factory=list)
    forbidden_imports: list[str] = Field(default_factory=list)
    max_file_lines: int = Field(default=600, ge=20, le=10_000)
    max_function_complexity: int = Field(default=15, ge=1, le=100)
    allow_data_access: bool = False


class InterfaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    source: str = Field(min_length=1, max_length=500)
    generated_paths: list[str] = Field(min_length=1)
    digest_file: str = Field(min_length=1, max_length=500)


class ContractCommands(BaseModel):
    model_config = ConfigDict(extra="forbid")

    install: list[list[str]] = Field(default_factory=list)
    format: list[list[str]] = Field(default_factory=list)
    lint: list[list[str]] = Field(default_factory=list)
    type_check: list[list[str]] = Field(default_factory=list)
    test: list[list[str]] = Field(default_factory=list)
    architecture: list[list[str]] = Field(default_factory=list)
    integration: list[list[str]] = Field(default_factory=list)
    build: list[list[str]] = Field(default_factory=list)
    acceptance: list[list[str]] = Field(default_factory=list)
    security: list[list[str]] = Field(default_factory=list)

    def gate_commands(self) -> list[list[str]]:
        return [
            *self.lint,
            *self.type_check,
            *self.test,
            *self.architecture,
            *self.integration,
            *self.build,
            *self.security,
        ]


class MigrationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=list)
    filename_pattern: str = r"^(?P<sequence>[0-9]{4})_[a-z0-9_-]+\.(?:sql|py)$"
    rollback_required: bool = False
    rollback_suffix: str = ".down.sql"
    compatibility_window: str = "expand-contract"


class ArtifactContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_files: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    dockerfile: str = "Dockerfile"
    compose_file: str = "compose.yaml"
    health_path: str = Field(default="/health", pattern=r"^/")


class RepositoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forbidden_globs: list[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.local",
            ".env.*.local",
            "*.pem",
            "*.key",
            "node_modules/**",
            "__pycache__/**",
            "dist/**",
            "build/**",
        ]
    )
    allowed_binary_extensions: list[str] = Field(
        default_factory=lambda: [".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"]
    )
    max_binary_bytes: int = Field(default=5_000_000, ge=1_024, le=1_000_000_000)
    binary_license_file: str = "docs/assets-license.md"
    secret_patterns: list[str] = Field(
        default_factory=lambda: [
            r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
            r"ghp_[A-Za-z0-9]{30,}",
            r"sk-[A-Za-z0-9_-]{30,}",
            r"AKIA[0-9A-Z]{16}",
        ]
    )


class ManualReviewRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    description: str = Field(min_length=1, max_length=2_000)
    required_evidence: str = Field(min_length=1, max_length=1_000)


class ProjectContract(ProductionRecord):
    object_type: Literal["project_contract"] = "project_contract"
    contract_id: str = Field(pattern=r"^pc_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(ge=1)
    status: ProjectContractStatus = ProjectContractStatus.DRAFT
    previous_version: int | None = Field(default=None, ge=1)
    profile_id: Literal[
        "fullstack-web", "backend-api", "frontend-spa", "python-service", "worker-service"
    ]
    schema_version: str = "1.0"
    inferred: bool = False
    repository_commit: str = ""
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    directory_structure: list[str] = Field(default_factory=list)
    modules: list[ModuleContract] = Field(min_length=1)
    interfaces: list[InterfaceContract] = Field(default_factory=list)
    commands: ContractCommands = Field(default_factory=ContractCommands)
    migrations: MigrationContract = Field(default_factory=MigrationContract)
    artifacts: ArtifactContract = Field(default_factory=ArtifactContract)
    repository_policy: RepositoryPolicy = Field(default_factory=RepositoryPolicy)
    documentation_files: list[str] = Field(default_factory=list)
    environment_example: str = ".env.example"
    manual_review: list[ManualReviewRule] = Field(default_factory=list)
    manual_evidence: dict[str, str] = Field(default_factory=dict)
    approved_by: str = ""
    approved_at: str = ""

    @model_validator(mode="after")
    def contract_is_coherent(self):
        names = [module.name for module in self.modules]
        if len(names) != len(set(names)):
            raise ValueError("project contract module names must be unique")
        known = set(names)
        for module in self.modules:
            unknown = (set(module.may_import) | set(module.forbidden_imports)) - known
            if unknown:
                raise ValueError(f"module {module.name} references unknown modules: {unknown}")
            if module.name in module.may_import:
                raise ValueError(f"module {module.name} cannot import itself")
        if self.version == 1 and self.previous_version is not None:
            raise ValueError("first ProjectContract version cannot reference a previous version")
        if self.version > 1 and self.previous_version != self.version - 1:
            raise ValueError("new ProjectContract version must reference its immediate predecessor")
        return self
