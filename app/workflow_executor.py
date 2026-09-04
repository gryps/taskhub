from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from typing import Any

from psycopg.types.json import Jsonb

from app.planner import PlannerRequest, run_structured_role
from app.taskhub import connect, now_utc, redact, transition_workflow


AUTOMATED_STAGES = {
    "review": "reviewer",
    "risk": "risk",
    "awaiting_supervision": "supervisor",
}

STAGE_SCHEMAS = {
    "review": (
        '{"verdict":"pass|rework|block","summary":string,'
        '"findings":[string],"test_gaps":[string]}'
    ),
    "risk": (
        '{"verdict":"clear|block|escalate","summary":string,'
        '"risk_level":"low|medium|high","risks":[string],"mitigations":[string]}'
    ),
    "awaiting_supervision": (
        '{"verdict":"approve|rework|block","summary":string,'
        '"rationale":string,"required_actions":[string]}'
    ),
}

STAGE_OUTPUT_SCHEMAS = {
    "review": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "summary", "findings", "test_gaps"],
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "rework", "block"]},
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "string"}},
            "test_gaps": {"type": "array", "items": {"type": "string"}},
        },
    },
    "risk": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "summary", "risk_level", "risks", "mitigations"],
        "properties": {
            "verdict": {"type": "string", "enum": ["clear", "block", "escalate"]},
            "summary": {"type": "string"},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "risks": {"type": "array", "items": {"type": "string"}},
            "mitigations": {"type": "array", "items": {"type": "string"}},
        },
    },
    "awaiting_supervision": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "summary", "rationale", "required_actions"],
        "properties": {
            "verdict": {"type": "string", "enum": ["approve", "rework", "block"]},
            "summary": {"type": "string"},
            "rationale": {"type": "string"},
            "required_actions": {"type": "array", "items": {"type": "string"}},
        },
    },
}

ALLOWED_VERDICTS = {
    "review": {"pass", "rework", "block"},
    "risk": {"clear", "block", "escalate"},
    "awaiting_supervision": {"approve", "rework", "block"},
}


def retry_delay() -> timedelta:
    return timedelta(seconds=max(30, int(os.getenv("WORKFLOW_ROLE_RETRY_SECONDS", "300"))))


def max_attempts() -> int:
    return max(1, int(os.getenv("WORKFLOW_ROLE_MAX_ATTEMPTS", "3")))


def role_timeout() -> timedelta:
    return timedelta(seconds=max(120, int(os.getenv("WORKFLOW_ROLE_STALE_SECONDS", "900"))))


def action_for_result(state: str, verdict: str) -> str | None:
    actions = {
        "review": {"pass": "review_pass", "rework": "review_reject", "block": "block"},
        "risk": {"clear": "risk_clear", "block": "risk_block", "escalate": "risk_block"},
    }
    return actions.get(state, {}).get(verdict)


def validate_stage_output(state: str, output: dict[str, Any]) -> dict[str, Any]:
    verdict = str(output.get("verdict") or "").strip().lower()
    if verdict not in ALLOWED_VERDICTS[state]:
        raise ValueError(f"invalid {state} verdict: {verdict or 'missing'}")
    summary = str(output.get("summary") or "").strip()
    if not summary:
        raise ValueError(f"{state} output is missing summary")
    normalized = {**output, "verdict": verdict, "summary": summary}
    for key in ("findings", "test_gaps", "risks", "mitigations", "required_actions"):
        if key in normalized and not isinstance(normalized[key], list):
            raise ValueError(f"{state} output field {key} must be a list")
    return redact(normalized)


def stage_messages(claim: dict[str, Any]) -> list[dict[str, str]]:
    state = claim["state"]
    role = claim["role"]
    snapshot = {
        "workflow": {
            "id": str(claim["workflow_id"]),
            "project": claim["project"],
            "summary": claim["summary"],
            "risk_level": claim["risk_level"],
            "iteration": claim["iteration"],
            "max_iterations": claim["max_iterations"],
            "context": redact(claim.get("context") or {}),
        },
        "tasks": redact(claim.get("tasks") or []),
    }
    system = (
        "You are the post-implementation role in a LangGraph TaskHub workflow. "
        f"Current role: {role}. Current stage: {state}. "
        "Judge only from the supplied evidence. Do not claim tests passed unless task evidence proves it. "
        "Return only one valid JSON object without markdown. "
        f"Required schema: {STAGE_SCHEMAS[state]}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False, default=str)[:16000]},
    ]


def claim_next_role_run() -> dict[str, Any] | None:
    timestamp = now_utc()
    stale_before = timestamp - role_timeout()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select workflow.*, transition.id as entry_transition_id
                from taskhub_workflows workflow
                join lateral (
                    select item.id
                    from taskhub_workflow_transitions item
                    where item.workflow_id = workflow.id and item.to_state = workflow.state
                    order by item.created_at desc
                    limit 1
                ) transition on true
                left join taskhub_workflow_role_runs run
                  on run.workflow_id = workflow.id
                 and run.entry_transition_id = transition.id
                where workflow.state in ('review', 'risk', 'awaiting_supervision')
                  and (
                    run.id is null
                    or (run.status = 'failed' and run.attempt_count < %s and run.next_retry_at <= %s)
                    or (run.status = 'running' and run.started_at < %s and run.attempt_count < %s)
                  )
                order by workflow.updated_at
                limit 1
                for update of workflow skip locked
                """,
                (max_attempts(), timestamp, stale_before, max_attempts()),
            )
            workflow = cur.fetchone()
            if not workflow:
                return None
            role = AUTOMATED_STAGES[workflow["state"]]
            run_id = uuid.uuid4()
            cur.execute(
                """
                insert into taskhub_workflow_role_runs
                    (id, workflow_id, entry_transition_id, state, role, iteration, status,
                     attempt_count, started_at)
                values (%s, %s, %s, %s, %s, %s, 'running', 1, %s)
                on conflict (workflow_id, entry_transition_id) do update
                set status = 'running', attempt_count = taskhub_workflow_role_runs.attempt_count + 1,
                    started_at = excluded.started_at, completed_at = null, next_retry_at = null,
                    error = '{}'::jsonb
                where taskhub_workflow_role_runs.status = 'failed'
                   or taskhub_workflow_role_runs.started_at < %s
                returning id, attempt_count
                """,
                (
                    run_id,
                    workflow["id"],
                    workflow["entry_transition_id"],
                    workflow["state"],
                    role,
                    workflow["iteration"],
                    timestamp,
                    stale_before,
                ),
            )
            claimed_run = cur.fetchone()
            if not claimed_run:
                return None
            cur.execute(
                """
                select id, type, title, state, input, metadata, result, error, worker_id, updated_at
                from taskhub_tasks
                where metadata->>'workflow_id' = %s
                order by created_at
                """,
                (str(workflow["id"]),),
            )
            tasks = cur.fetchall()
        conn.commit()
    return {
        **workflow,
        "workflow_id": workflow["id"],
        "run_id": claimed_run["id"],
        "attempt_count": claimed_run["attempt_count"],
        "role": role,
        "tasks": tasks,
    }


def complete_role_run(claim: dict[str, Any], output: dict[str, Any]) -> None:
    timestamp = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update taskhub_workflow_role_runs
                set status = 'succeeded', provider = %s, model = %s, verdict = %s,
                    summary = %s, output = %s, completed_at = %s, next_retry_at = null
                where id = %s and status = 'running'
                """,
                (
                    output.get("provider") or output.get("source"),
                    output.get("model"),
                    output["verdict"],
                    output["summary"],
                    Jsonb(output),
                    timestamp,
                    claim["run_id"],
                ),
            )
            if cur.rowcount != 1:
                return
            action = action_for_result(claim["state"], output["verdict"])
            if action:
                if claim["state"] == "risk":
                    cur.execute(
                        "update taskhub_workflows set risk_level = %s where id = %s and state = 'risk'",
                        (output.get("risk_level") or "medium", claim["workflow_id"]),
                    )
                transition_workflow(
                    cur,
                    claim["workflow_id"],
                    action,
                    f"model:{claim['role']}",
                    output["summary"],
                    output,
                )
            else:
                cur.execute("select * from taskhub_workflows where id = %s for update", (claim["workflow_id"],))
                workflow = cur.fetchone()
                if workflow and workflow["state"] == claim["state"]:
                    context = {**(workflow.get("context") or {})}
                    outputs = {**(context.get("stage_outputs") or {})}
                    outputs[claim["role"]] = output
                    context["stage_outputs"] = outputs
                    cur.execute(
                        "update taskhub_workflows set context = %s, updated_at = %s where id = %s",
                        (Jsonb(context), timestamp, claim["workflow_id"]),
                    )
        conn.commit()


def fail_role_run(claim: dict[str, Any], error: dict[str, Any]) -> None:
    timestamp = now_utc()
    next_retry_at = timestamp + retry_delay() if claim["attempt_count"] < max_attempts() else None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update taskhub_workflow_role_runs
                set status = 'failed', error = %s, completed_at = %s, next_retry_at = %s
                where id = %s and status = 'running'
                """,
                (Jsonb(redact(error)), timestamp, next_retry_at, claim["run_id"]),
            )
        conn.commit()


def run_automation_cycle() -> bool:
    claim = claim_next_role_run()
    if not claim:
        return False
    try:
        request = PlannerRequest(
            requirement=str(claim["summary"]),
            project=str(claim["project"]),
            title=f"workflow {claim['workflow_id']} {claim['state']}",
            priority=80,
            pipeline_id=claim.get("pipeline_id"),
        )
        raw_output = run_structured_role(
            claim["role"], request, stage_messages(claim), STAGE_OUTPUT_SCHEMAS[claim["state"]]
        )
        if raw_output.get("status") != "succeeded":
            fail_role_run(claim, raw_output)
            return True
        complete_role_run(claim, validate_stage_output(claim["state"], raw_output))
    except Exception as exc:
        fail_role_run(claim, {"reason": "executor_error", "error_type": exc.__class__.__name__, "message": str(exc)[:500]})
    return True
