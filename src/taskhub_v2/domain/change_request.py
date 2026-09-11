from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from taskhub_v2.domain.production_base import ProductionRecord


class ChangeRequestStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"


class ChangeRequest(ProductionRecord):
    object_type: Literal["change_request"] = "change_request"
    change_request_id: str = Field(pattern=r"^cr_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(default=1, ge=1)
    status: ChangeRequestStatus = ChangeRequestStatus.PROPOSED
    reason: str = Field(min_length=1, max_length=4_000)
    source_event: str = Field(min_length=1, max_length=200)
    source_run_id: str = ""
    source_plan_id: str = ""
    source_plan_version: int = Field(default=1, ge=1)
    automatic: bool = False
    revision_number: int = Field(default=1, ge=1)
    affected_task_ids: list[str] = Field(default_factory=list)
    superseded_task_ids: list[str] = Field(default_factory=list)
    added_task_ids: list[str] = Field(default_factory=list)
    regression_scope: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    impact_reasons: dict[str, list[str]] = Field(default_factory=dict)
    plan_diff: dict[str, Any] = Field(default_factory=dict)
    approved_by: str = ""
    approved_at: datetime | None = None
    applied_at: datetime | None = None
