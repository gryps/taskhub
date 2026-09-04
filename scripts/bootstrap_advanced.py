from __future__ import annotations

import argparse
import uuid

from psycopg.types.json import Jsonb

from app.advanced import init_advanced
from app.taskhub import canonical_hash, connect, init_taskhub, now_utc


DEFAULT_WORKSPACES = [
    {
        "pipeline_id": "fb1e3a1a-ab2a-48c3-a6b7-d2cd82d15c4d",
        "worker_id": "worker-31-31-implementation-a",
        "workspace_id": "douyin-implementation-a",
        "workspace_path": "/home/gryps/worktrees/douyin-listing-workbench-implementation-a",
        "branch": "taskhub/implementation-31-31-01",
        "baseline_commit": "3319b250de81e2def64eda6e9c75ee17d1848f2e",
    },
    {
        "pipeline_id": "12602378-68aa-40d0-ae9d-78ec17a3c0f8",
        "worker_id": "worker-31-31-implementation",
        "workspace_id": "douyin-implementation-b",
        "workspace_path": "/home/gryps/worktrees/douyin-listing-workbench-implementation",
        "branch": "taskhub/implementation-31-31-02",
        "baseline_commit": "3319b250de81e2def64eda6e9c75ee17d1848f2e",
    },
]


DEFAULT_MEMORIES = [
    ("constraint", "代码权威源", "项目代码权威源位于 192.168.31.17，本项目禁止从云端主机索取资源。", 100, ["architecture", "source"]),
    ("constraint", "双生产线隔离", "A/B 实施流水线使用独立 WSL、工作区和分支，不共享节点计算资源。", 95, ["pipeline", "isolation"]),
    ("decision", "人工发布边界", "发布、上线、凭据变更和生产代码应用必须由人工批准。", 100, ["governance", "safety"]),
]


def bootstrap(args: argparse.Namespace) -> None:
    init_taskhub()
    init_advanced()
    timestamp = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into taskhub_projects
                (id,slug,name,state,source_host,source_root,default_branch,metadata,created_at,updated_at)
                values(%s,%s,%s,'active',%s,%s,%s,%s,%s,%s)
                on conflict(slug) do update set name=excluded.name,source_host=excluded.source_host,
                source_root=excluded.source_root,default_branch=excluded.default_branch,updated_at=excluded.updated_at
                returning id""",
                (
                    uuid.uuid4(), args.project, args.name, args.source_host, args.source_root,
                    args.source_branch, Jsonb({"authority": "local_lan", "cloud_access": False}),
                    timestamp, timestamp,
                ),
            )
            project_id = cur.fetchone()["id"]
            for workspace in DEFAULT_WORKSPACES:
                cur.execute(
                    "select project,workspace_id from taskhub_pipelines where id=%s",
                    (workspace["pipeline_id"],),
                )
                pipeline = cur.fetchone()
                if not pipeline or pipeline["project"] != args.project or pipeline["workspace_id"] != workspace["workspace_id"]:
                    raise RuntimeError(f"pipeline binding mismatch: {workspace['workspace_id']}")
                cur.execute(
                    """insert into taskhub_project_workspaces
                    (id,project_id,pipeline_id,worker_id,workspace_id,workspace_path,branch,baseline_commit,
                     state,last_sync_at,created_at,updated_at)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,'ready',%s,%s,%s)
                    on conflict(workspace_id) do update set pipeline_id=excluded.pipeline_id,
                    worker_id=excluded.worker_id,workspace_path=excluded.workspace_path,branch=excluded.branch,
                    baseline_commit=excluded.baseline_commit,state='ready',updated_at=excluded.updated_at""",
                    (
                        uuid.uuid4(), project_id, workspace["pipeline_id"], workspace["worker_id"],
                        workspace["workspace_id"], workspace["workspace_path"], workspace["branch"],
                        workspace["baseline_commit"], timestamp, timestamp, timestamp,
                    ),
                )
            context = {
                "verification": "git executable unavailable on authority host",
                "authority": args.source_host,
            }
            snapshot = {
                "branch": args.source_branch,
                "commit": args.source_commit,
                "dirty": True,
                "status_hash": None,
                "change_count": 0,
                "context": context,
            }
            digest = canonical_hash(snapshot)
            cur.execute(
                "select 1 from taskhub_context_snapshots where project_id=%s and content_hash=%s",
                (project_id, digest),
            )
            if not cur.fetchone():
                cur.execute(
                    "select coalesce(max(version),0)+1 version from taskhub_context_snapshots where project_id=%s",
                    (project_id,),
                )
                version = cur.fetchone()["version"]
                cur.execute(
                    """insert into taskhub_context_snapshots
                    (id,project_id,version,branch,commit,dirty,status_hash,change_count,context,
                     content_hash,created_by,created_at)
                    values(%s,%s,%s,%s,%s,true,null,0,%s,%s,'deployment:advanced-stage',%s)""",
                    (
                        uuid.uuid4(), project_id, version, args.source_branch, args.source_commit,
                        Jsonb(context), digest, timestamp,
                    ),
                )
            for kind, title, content, importance, tags in DEFAULT_MEMORIES:
                digest = canonical_hash({"title": title, "content": content})
                cur.execute(
                    """insert into taskhub_memories
                    (id,project_id,kind,title,content,importance,tags,content_hash,active,
                     created_by,created_at,updated_at)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,true,'deployment:advanced-stage',%s,%s)
                    on conflict(project_id,kind,content_hash) do update set active=true,updated_at=excluded.updated_at""",
                    (
                        uuid.uuid4(), project_id, kind, title, content, importance, tags,
                        digest, timestamp, timestamp,
                    ),
                )
            cur.execute(
                """insert into taskhub_governance_policies
                (project,autonomy_enabled,allowed_actions,max_risk,require_human_release,
                 require_human_publish,updated_by,updated_at)
                values(%s,false,%s,'low',true,true,'deployment:advanced-stage',%s)
                on conflict(project) do nothing""",
                (
                    args.project,
                    ["refresh_context", "requeue_expired_lease", "retry_task", "run_quality_evaluation"],
                    timestamp,
                ),
            )
            cur.execute(
                """insert into taskhub_project_budgets
                (project,monthly_budget_usd,warning_ratio,hard_limit,updated_by,updated_at)
                values(%s,0,0.8,false,'deployment:advanced-stage',%s)
                on conflict(project) do nothing""",
                (args.project, timestamp),
            )
        conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap the deployed advanced-stage project registry.")
    parser.add_argument("--project", default="douyin-listing-workbench")
    parser.add_argument("--name", default="抖音电商工作台")
    parser.add_argument("--source-host", default="192.168.31.17")
    parser.add_argument("--source-root", default="/home/gryps/.openclaw/workspace/douyin-listing-workbench")
    parser.add_argument("--source-branch", default="openclaw/be012-review-r1")
    parser.add_argument("--source-commit", default="322dbc1d700ab602ed76c89000352374199c8298")
    return parser.parse_args()


if __name__ == "__main__":
    bootstrap(parse_args())
    print("advanced-bootstrap-ok")
