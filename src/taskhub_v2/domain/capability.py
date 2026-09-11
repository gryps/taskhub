from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from taskhub_v2.domain.production_base import ProductionRecord

PackType = Literal[
    "frontend-style",
    "frontend-components",
    "frontend-layout",
    "brand",
    "project-architecture",
    "testing",
    "security",
    "delivery",
]


class PackCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskhub_spec: list[str] = Field(default_factory=lambda: ["1.1"])
    project_profiles: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    incompatible_with: list[str] = Field(default_factory=list)


class CapabilityPack(ProductionRecord):
    object_type: Literal["capability_pack"] = "capability_pack"
    pack_id: str = Field(pattern=r"^pack_[A-Za-z0-9_-]{3,100}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$")
    status: Literal["draft", "trusted", "disabled", "rejected"] = "draft"
    name: str = Field(default="", max_length=200)
    pack_type: PackType
    source: Literal["builtin", "admin-import"]
    source_uri: str = Field(default="", max_length=500)
    summary: str = Field(min_length=1, max_length=2_000)
    compatibility: PackCompatibility = Field(default_factory=PackCompatibility)
    files: list[str] = Field(default_factory=list, max_length=500)
    assets: list[str] = Field(default_factory=list, max_length=500)
    templates: list[str] = Field(default_factory=list, max_length=500)
    rules: list[str] = Field(default_factory=list, max_length=500)
    validators: list[str] = Field(default_factory=list, max_length=100)
    license: str = Field(min_length=1, max_length=200)
    asset_license: str = Field(default="", max_length=500)
    contains_executable: bool = False
    required_permissions: list[str] = Field(default_factory=list, max_length=50)
    content: dict[str, Any] = Field(default_factory=dict)
    manifest_digest: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    trusted_by: str = ""
    trusted_at: datetime | None = None
    disabled_reason: str = Field(default="", max_length=1_000)

    @field_validator("files", "assets", "templates", "rules", "validators")
    @classmethod
    def safe_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            parts = value.replace("\\", "/").split("/")
            if not value or value.startswith(("/", "\\")) or ".." in parts:
                raise ValueError("capability pack paths must be relative and cannot traverse")
        return values

    @model_validator(mode="after")
    def executable_permissions_are_explicit(self):
        if self.contains_executable and not self.required_permissions:
            raise ValueError("executable capability packs must declare required permissions")
        if self.assets and not self.asset_license:
            raise ValueError("capability packs with assets must declare asset authorization")
        return self

    @property
    def ref(self) -> str:
        return f"{self.pack_id}@{self.version}"


class CapabilityPackLock(ProductionRecord):
    object_type: Literal["capability_pack_lock"] = "capability_pack_lock"
    lock_id: str = Field(pattern=r"^lock_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(ge=1)
    status: Literal["draft", "active", "superseded", "rejected"] = "draft"
    spec_id: str
    spec_version: int = Field(ge=1)
    pack_refs: list[str] = Field(min_length=1, max_length=20)
    compatibility_report: dict[str, Any] = Field(default_factory=dict)
    previous_version: int | None = Field(default=None, ge=1)
    migration_tasks: list[str] = Field(default_factory=list)
    activated_by: str = ""
    activated_at: datetime | None = None

    @model_validator(mode="after")
    def version_chain_is_explicit(self):
        if self.version == 1 and self.previous_version is not None:
            raise ValueError("first capability lock cannot reference a previous version")
        if self.version > 1 and self.previous_version != self.version - 1:
            raise ValueError("new capability lock must reference its immediate predecessor")
        if len(self.pack_refs) != len(set(self.pack_refs)):
            raise ValueError("capability lock references must be unique")
        exact_ref = r"^pack_[A-Za-z0-9_-]{3,100}@[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$"
        if any(not re.fullmatch(exact_ref, item) for item in self.pack_refs):
            raise ValueError("capability lock requires exact pack versions")
        return self


class ProjectDesignContract(ProductionRecord):
    object_type: Literal["project_design_contract"] = "project_design_contract"
    contract_id: str = Field(pattern=r"^pdc_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(ge=1)
    status: Literal["active", "superseded"] = "active"
    lock_id: str
    lock_version: int = Field(ge=1)
    spec_id: str
    spec_version: int = Field(ge=1)
    framework: str = ""
    pack_refs: list[str] = Field(min_length=1)
    design_tokens: dict[str, Any] = Field(default_factory=dict)
    component_rules: list[str] = Field(default_factory=list)
    layout_rules: list[str] = Field(default_factory=list)
    responsive_rules: list[str] = Field(default_factory=list)
    accessibility_rules: list[str] = Field(default_factory=list)
    brand_rules: list[str] = Field(default_factory=list)
    viewports: list[str] = Field(default_factory=lambda: ["1440x900", "768x1024", "390x844"])
    validation_evidence: list[str] = Field(default_factory=list)
    migration_tasks: list[str] = Field(default_factory=list)


class DesignRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    name: str
    summary: str
    pack_refs: list[str]
    compatibility: dict[str, Any]
    previews: list[dict[str, Any]]
