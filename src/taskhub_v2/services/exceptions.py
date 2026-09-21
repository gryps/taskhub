from __future__ import annotations

from typing import Any

from taskhub_v2.domain.models import RunStatus, TaskSummary

ACTION_LABELS = {
    "retry": "重试当前环节",
    "revise": "修订后重试",
    "recheck": "补充证据并复核",
    "manual": "提交人工证据",
    "cancel": "终止任务",
    "approve": "继续流程",
    "reassess": "重新评估",
}

CATEGORY_LABELS = {
    "project": "项目配置",
    "implementation": "编码实施",
    "acceptance": "测试验收",
    "evidence": "证据与监督",
    "publication": "Git 发布",
    "workflow": "工作流运行",
}

ACTION_CATEGORIES = {
    "implementation_recovery": "implementation",
    "acceptance_recovery": "acceptance",
    "publication_recovery": "publication",
    "risk_recovery": "evidence",
    "supervision_recovery": "evidence",
    "revision_limit": "evidence",
    "manual_intervention": "workflow",
}


class ExceptionCenterService:
    def __init__(self, task_index, projects):
        self.task_index = task_index
        self.projects = projects

    async def list(
        self, *, project_id: str = "", page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        source = await self.task_index.attention(
            project_id=project_id,
            page=page,
            page_size=page_size,
        )
        items = [self._view(item) for item in source.items]
        counts = {}
        for status in (RunStatus.WAITING, RunStatus.BLOCKED, RunStatus.FAILED):
            result = await self.task_index.list(
                project_id=project_id,
                status=status,
                page=1,
                page_size=1,
            )
            counts[status.value] = result.total
        return {
            "items": items,
            "total": source.total,
            "page": source.page,
            "page_size": source.page_size,
            "summary": {
                **counts,
                "visible_recoverable": sum(item["recoverable"] for item in items),
            },
        }

    def _view(self, task: TaskSummary) -> dict[str, Any]:
        project_id = task.rebound_project_id or task.project_id
        project_missing = self._project_missing(project_id)
        action = task.pending_action or {}
        action_type = str(action.get("type") or "")
        blocking = task.blocking_reason or {}
        category = "project" if project_missing else ACTION_CATEGORIES.get(
            action_type, self._stage_category(str(task.stage))
        )
        detail = str(
            blocking.get("detail")
            or action.get("detail")
            or action.get("title")
            or "任务需要处理后才能继续"
        )
        choices = [
            {"decision": choice, "label": ACTION_LABELS.get(choice, choice)}
            for choice in action.get("choices", [])
        ]
        if project_missing:
            choices = [
                {"decision": "rebind_project", "label": "重新绑定项目"},
                {"decision": "archive", "label": "归档任务"},
            ]
        recoverable = bool(choices)
        return {
            "exception_id": f"run:{task.run_id}",
            "run_id": task.run_id,
            "project_id": project_id,
            "requirement_summary": task.requirement_summary,
            "stage": task.stage,
            "status": task.status,
            "severity": (
                "critical"
                if task.status == RunStatus.FAILED
                else "high"
                if task.status == RunStatus.BLOCKED
                else "medium"
            ),
            "category": category,
            "category_label": CATEGORY_LABELS[category],
            "title": (
                "项目已失去绑定"
                if project_missing
                else str(action.get("title") or CATEGORY_LABELS[category] + "异常")
            ),
            "detail": detail,
            "recoverable": recoverable,
            "recommended_action": (
                choices[0]["label"] if choices else "查看任务证据并诊断根因"
            ),
            "actions": choices,
            "updated_at": task.updated_at,
        }

    def _project_missing(self, project_id: str) -> bool:
        if self.projects is None:
            return False
        try:
            self.projects.get(project_id)
            return False
        except LookupError:
            return True

    @staticmethod
    def _stage_category(stage: str) -> str:
        if stage.startswith("implementation"):
            return "implementation"
        if stage.startswith("acceptance") or stage == "browser_acceptance":
            return "acceptance"
        if stage in {"risk", "review", "supervision"}:
            return "evidence"
        if stage in {"merging", "merge_blocked"}:
            return "publication"
        return "workflow"
