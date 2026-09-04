from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.taskhub import TaskCreate, create_task_record
from app.project_snapshot import capture_project_snapshot


router = APIRouter(prefix="/graphs/douyin_stage1_requirement_flow", tags=["graphs"])


class RequirementInvoke(BaseModel):
    requirement: str | None = None
    input: str | None = None
    project: str = Field("douyin-listing-workbench", min_length=1)
    title: str | None = None
    priority: int = 50
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


def requirement_text(request: RequirementInvoke) -> str:
    text = request.requirement or request.input or ""
    return text.strip()


def task_title(prefix: str, title: str, max_length: int = 96) -> str:
    value = f"{prefix}: {title}".strip()
    return value[:max_length]


@router.post("/invoke")
def invoke_requirement_flow(request: RequirementInvoke) -> dict[str, Any]:
    text = requirement_text(request)
    title = request.title or (text[:40] if text else "新的项目需求")
    common_metadata = {
        "source": "douyin_stage1_requirement_flow",
        "project_repo": "192.168.31.17:/home/gryps/.openclaw/workspace/douyin-listing-workbench",
        "git_snapshot": capture_project_snapshot(),
        **request.metadata,
    }

    task_specs = [
        TaskCreate(
            project=request.project,
            type="ops.note",
            title=task_title("记录需求", title),
            priority=request.priority + 10,
            input={"note": text or title},
            metadata={**common_metadata, "stage": "requirement_received"},
        ),
        TaskCreate(
            project=request.project,
            type="project.context.sync",
            title=task_title("同步项目上下文", title),
            priority=request.priority + 5,
            input={
                "files": [
                    "AGENTS.md",
                    "PROJECT_STATE.md",
                    "CONTEXT_PACK.md",
                    "README.md",
                ],
                "reason": text or title,
            },
            metadata={**common_metadata, "stage": "context_sync"},
        ),
        TaskCreate(
            project=request.project,
            type="project.git.status",
            title=task_title("检查工作副本", title),
            priority=request.priority + 3,
            input={
                "reason": text or title,
            },
            metadata={**common_metadata, "stage": "workcopy_status"},
        ),
        TaskCreate(
            project=request.project,
            type="requirement.split",
            title=task_title("拆解需求", title),
            priority=request.priority,
            input={
                "requirement": text or title,
                "expected_output": "生成可执行任务、非目标、验收标准和风险清单",
            },
            metadata={**common_metadata, "stage": "planning"},
        ),
        TaskCreate(
            project=request.project,
            type="review.human",
            title=task_title("人工确认任务计划", title),
            priority=request.priority - 10,
            input={
                "requirement": text or title,
                "instruction": "确认是否进入开发执行；涉及生产、抖店登录态、真实商品素材时必须人工批准。",
                "approval_plan": [
                    {
                        "type": "quality.env.check",
                        "title": task_title("审批后环境检测", title),
                        "priority": request.priority - 12,
                        "input": {},
                        "metadata": {"stage": "post_approval_environment_gate"},
                    },
                    {
                        "type": "code.change",
                        "title": task_title("审批后代码变更计划", title),
                        "priority": request.priority - 13,
                        "input": {
                            "requirement": text or title,
                            "mode": "plan_only",
                            "constraints": [
                                "初级阶段只生成计划和风险清单，不直接修改文件。",
                                "不得触碰密钥、Cookie、Token、登录态和生产数据。",
                                "代码修改前必须由人工确认范围。",
                            ],
                        },
                        "metadata": {"stage": "post_approval_code_plan"},
                    },
                    {
                        "type": "code.diff.preview",
                        "title": task_title("审批后 diff 预览", title),
                        "priority": request.priority - 14,
                        "input": {
                            "paths": [],
                            "reason": "读取当前工作副本 diff，供人工判断是否存在待审改动。",
                        },
                        "metadata": {"stage": "post_approval_diff_preview"},
                    },
                    {
                        "type": "test.run",
                        "title": task_title("审批后 API lint", title),
                        "priority": request.priority - 15,
                        "input": {"command": "api.lint"},
                        "metadata": {"stage": "post_approval_quality_gate"},
                    }
                ],
            },
            metadata={**common_metadata, "stage": "human_gate"},
        ),
    ]

    if request.idempotency_key:
        for index, spec in enumerate(task_specs):
            spec.idempotency_key = f"graph:{request.idempotency_key}:{index}"
    tasks = [create_task_record(spec, actor="douyin_stage1_requirement_flow", reason="graph created task") for spec in task_specs]

    return {
        "graph": "douyin_stage1_requirement_flow",
        "project": request.project,
        "requirement": text,
        "created": len(tasks),
        "tasks": [
            {
                "id": task["id"],
                "type": task["type"],
                "title": task["title"],
                "state": task["state"],
                "priority": task["priority"],
            }
            for task in tasks
        ],
        "next": "在 TaskHub 任务列表中查看 pending/running/succeeded 状态；初级阶段不会自动修改生产。",
    }
