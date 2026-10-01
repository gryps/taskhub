from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taskhub_v2.domain.production_base import ProductionRecord


class EngineeringPolicyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class PolicyExceptionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class EngineeringRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,100}$")
    title: str = Field(min_length=1, max_length=200)
    category: Literal[
        "foundation",
        "architecture",
        "quality",
        "frontend",
        "security",
        "delivery",
    ]
    applies_to: list[str] = Field(default_factory=lambda: ["*"])
    blocking: bool = True
    instruction: str = Field(min_length=1, max_length=4_000)
    required_paths: list[str] = Field(default_factory=list)
    required_command_groups: list[
        Literal[
            "format",
            "lint",
            "type_check",
            "test",
            "architecture",
            "integration",
            "build",
            "acceptance",
            "security",
        ]
    ] = Field(default_factory=list)
    max_file_lines: int | None = Field(default=None, ge=20, le=10_000)
    max_function_lines: int | None = Field(default=None, ge=10, le=2_000)
    max_complexity: int | None = Field(default=None, ge=1, le=100)
    manual_evidence: str = Field(default="", max_length=2_000)

    def applies(self, profile_id: str) -> bool:
        return "*" in self.applies_to or profile_id in self.applies_to


class EngineeringPolicy(ProductionRecord):
    object_type: Literal["engineering_policy"] = "engineering_policy"
    policy_id: str = Field(pattern=r"^ep_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(ge=1)
    previous_version: int | None = Field(default=None, ge=1)
    status: EngineeringPolicyStatus = EngineeringPolicyStatus.DRAFT
    name: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(default="", max_length=500)
    source_digest: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    rules: list[EngineeringRule] = Field(min_length=1)
    activated_by: str = ""
    activated_at: str = ""

    @model_validator(mode="after")
    def version_chain_is_explicit(self):
        if self.version == 1 and self.previous_version is not None:
            raise ValueError("first engineering policy cannot reference a previous version")
        if self.version > 1 and self.previous_version != self.version - 1:
            raise ValueError("engineering policy must reference its immediate predecessor")
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("engineering policy rule identifiers must be unique")
        return self


class PolicyBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = ""
    policy_version: int | None = Field(default=None, ge=1)
    policy_digest: str = ""
    rule_ids: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    bound_at: str = ""

    @model_validator(mode="after")
    def binding_is_complete(self):
        values = (self.policy_id, self.policy_version, self.policy_digest)
        if any(values) and not all(values):
            raise ValueError("engineering policy binding is incomplete")
        return self


class PolicyException(ProductionRecord):
    object_type: Literal["policy_exception"] = "policy_exception"
    exception_id: str = Field(pattern=r"^px_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(default=1, ge=1)
    status: PolicyExceptionStatus = PolicyExceptionStatus.PROPOSED
    policy_id: str
    policy_version: int = Field(ge=1)
    rule_ids: list[str] = Field(min_length=1)
    scope: Literal["project", "plan", "task"] = "project"
    scope_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=4_000)
    risk: str = Field(min_length=10, max_length=4_000)
    controls: list[str] = Field(min_length=1)
    recovery_condition: str = Field(min_length=10, max_length=2_000)
    expires_at: datetime
    decided_by: str = ""
    decided_at: str = ""

    @model_validator(mode="after")
    def scoped_exceptions_name_targets(self):
        if self.scope != "project" and not self.scope_ids:
            raise ValueError("plan and task exceptions require scope identifiers")
        if len(self.rule_ids) != len(set(self.rule_ids)):
            raise ValueError("policy exception rule identifiers must be unique")
        return self
