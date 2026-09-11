from typing import Literal

from pydantic import BaseModel, Field


class GateFinding(BaseModel):
    gate_id: str
    category: str
    status: Literal["passed", "failed", "warning", "manual"]
    summary: str
    path: str = ""
    detail: str = ""


class ProjectGateReport(BaseModel):
    project_id: str
    contract_id: str
    contract_version: int
    repository_commit: str
    status: Literal["passed", "failed", "manual_review"]
    findings: list[GateFinding] = Field(default_factory=list)


class ProjectContractGateError(RuntimeError):
    reason = "project_contract_gate_failed"

    def __init__(self, report: ProjectGateReport):
        self.report = report
        failed = [item for item in report.findings if item.status == "failed"]
        self.detail = "; ".join(item.summary for item in failed[:8]) or "项目合同门禁未通过"
        super().__init__(self.detail)
