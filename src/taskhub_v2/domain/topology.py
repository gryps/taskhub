from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TopologyStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    INVALID = "invalid"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class TopologyNodeType(StrEnum):
    PROJECT = "project"
    CONTROLLER = "controller"
    EXECUTION = "execution"
    TEST = "test"
    PREPRODUCTION = "preproduction"
    RESOURCE_POOL = "resource_pool"


class TopologyEdgeType(StrEnum):
    GOVERNED_BY = "governed_by"
    EXECUTES_ON = "executes_on"
    VERIFIED_BY = "verified_by"
    ACCEPTED_BY = "accepted_by"
    FALLBACK_TO = "fallback_to"


class CanvasPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)


class TopologyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    node_type: TopologyNodeType
    label: str = Field(min_length=1, max_length=120)
    resource_id: str = Field(default="", max_length=128)
    member_ids: list[str] = Field(default_factory=list, max_length=100)
    required_capabilities: list[str] = Field(default_factory=list, max_length=50)
    position: CanvasPosition = Field(default_factory=lambda: CanvasPosition(x=0, y=0))

    @model_validator(mode="after")
    def validate_binding(self):
        if self.node_type == TopologyNodeType.RESOURCE_POOL:
            if self.resource_id:
                raise ValueError("resource pools use member_ids instead of resource_id")
        elif self.member_ids:
            raise ValueError("only resource pools may contain member_ids")
        return self


class TopologyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source: str
    target: str
    edge_type: TopologyEdgeType
    priority: int = Field(default=100, ge=0, le=10_000)


class TopologyViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = 0
    y: float = 0
    zoom: float = Field(default=1, ge=0.1, le=4)


class ValidationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    level: str = Field(pattern=r"^(error|warning)$")
    subject_id: str = ""


class ProductionTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topology_id: str
    project_id: str
    version: int = Field(ge=1)
    status: TopologyStatus = TopologyStatus.DRAFT
    nodes: list[TopologyNode] = Field(default_factory=list, max_length=500)
    edges: list[TopologyEdge] = Field(default_factory=list, max_length=1_000)
    viewport: TopologyViewport = Field(default_factory=TopologyViewport)
    findings: list[ValidationFinding] = Field(default_factory=list)
    created_by: str
    activated_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    activated_at: datetime | None = None
    content_digest: str = ""


def topology_digest(topology: ProductionTopology) -> str:
    payload: dict[str, Any] = topology.model_dump(
        mode="json",
        exclude={"content_digest", "updated_at", "findings", "status", "activated_at"},
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def with_topology_digest(topology: ProductionTopology) -> ProductionTopology:
    return topology.model_copy(update={"content_digest": topology_digest(topology)})
