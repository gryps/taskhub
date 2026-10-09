#!/usr/bin/env python3
"""Discover, recover, create and resume TaskHub project runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from taskhub_client import TaskHubClient, TaskHubClientError  # noqa: E402

DEFAULT_CONNECTION_FILE = Path.home() / ".codex" / "taskhub-v2-connection.json"
TERMINAL_STATUSES = {"completed", "rejected", "failed"}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--connection-file", default=str(DEFAULT_CONNECTION_FILE))
    root.add_argument("--timeout-seconds", type=float, default=15)
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("projects", help="List registered projects")
    runs = commands.add_parser("runs", help="List project runs")
    _project_arguments(runs)
    runs.add_argument("--production-line")
    runs.add_argument("--include-archived", action="store_true")

    status = commands.add_parser("status", help="Read one run")
    status.add_argument("--run-id", required=True)
    status.add_argument("--full", action="store_true", help="Print the complete run payload")

    resume = commands.add_parser("resume", help="Explicitly resume a waiting or blocked run")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--decision", required=True)
    resume.add_argument("--comment", default="")

    create = commands.add_parser("create-run", help="Create a run from an approved product spec")
    _project_arguments(create)
    _creation_arguments(create)

    ensure = commands.add_parser(
        "ensure-run", help="Recover the unique active run, or create one when none exists"
    )
    _project_arguments(ensure)
    _creation_arguments(ensure)
    return root


def _project_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--project", help="Project id/name/path; defaults to current directory")


def _creation_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--production-line", default="default")
    requirement = command.add_mutually_exclusive_group()
    requirement.add_argument("--requirement")
    requirement.add_argument("--requirement-file")
    command.add_argument("--spec-id")
    command.add_argument("--spec-version", type=int)


def project_hints(value: str | None) -> set[str]:
    path = Path(value or ".").expanduser().resolve()
    hints = {str(path), path.name}
    if value:
        hints.add(value)
    if (path / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        remote = result.stdout.strip().rstrip("/")
        if remote:
            hints.add(remote)
            hints.add(Path(remote.removesuffix(".git")).name)
    return {item.casefold() for item in hints if item}


def resolve_project(client: TaskHubClient, value: str | None) -> dict:
    projects = client.get("api/projects").get("projects", [])
    hints = project_hints(value)
    exact = [item for item in projects if str(item.get("id", "")).casefold() in hints]
    if len(exact) == 1:
        return exact[0]
    matches = []
    for item in projects:
        candidates = {
            str(item.get("name", "")).casefold(),
            str(item.get("repository", "")).casefold(),
            Path(str(item.get("repository", ""))).name.casefold(),
        }
        if candidates & hints:
            matches.append(item)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise TaskHubClientError("no registered project uniquely matches the supplied project")
    raise TaskHubClientError("multiple registered projects match; pass the exact project id")


def list_runs(client: TaskHubClient, project_id: str, production_line: str | None, archived=False):
    payload = client.get(
        "api/runs",
        query={
            "project_id": project_id,
            "production_line": production_line,
            "include_archived": str(archived).lower(),
            "page_size": 200,
        },
    )
    return payload.get("items", [])


def requirement_text(args: argparse.Namespace) -> str:
    if args.requirement_file:
        value = Path(args.requirement_file).expanduser().read_text(encoding="utf-8").strip()
    else:
        value = (args.requirement or "按当前已批准产品规格执行").strip()
    if len(value) < 3:
        raise ValueError("requirement must contain at least 3 characters")
    return value


def approved_spec(
    client: TaskHubClient, project_id: str, args: argparse.Namespace
) -> tuple[str, int]:
    if bool(args.spec_id) != bool(args.spec_version):
        raise ValueError("--spec-id and --spec-version must be supplied together")
    if args.spec_id:
        payload = client.get(
            f"api/product-specs/{args.spec_id}/versions/{args.spec_version}",
            query={"project_id": project_id},
        )
    else:
        payload = client.get("api/product-specs/current", query={"project_id": project_id})
    spec = payload.get("product_spec")
    if not isinstance(spec, dict):
        raise TaskHubClientError("project has no current product spec")
    if spec.get("status") != "approved":
        raise TaskHubClientError("product spec is not approved")
    return str(spec["spec_id"]), int(spec["version"])


def create_run(client: TaskHubClient, project: dict, args: argparse.Namespace) -> dict:
    spec_id, version = approved_spec(client, project["id"], args)
    return client.post(
        "api/runs",
        {
            "project_id": project["id"],
            "production_line": args.production_line,
            "requirement": requirement_text(args),
            "product_spec_id": spec_id,
            "product_spec_version": version,
        },
    )


def run_summary(run: dict) -> dict:
    tasks = run.get("production_tasks") or []
    task_counts: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status", "unknown"))
        task_counts[status] = task_counts.get(status, 0) + 1
    return {
        key: run.get(key)
        for key in (
            "run_id",
            "project_id",
            "production_line",
            "stage",
            "status",
            "updated_at",
            "pending_action",
            "blocking_reason",
            "revision_count",
            "max_revision_attempts",
        )
    } | {"task_counts": task_counts}


def execute(client: TaskHubClient, args: argparse.Namespace):
    if args.command == "projects":
        return client.get("api/projects")
    if args.command == "status":
        run = client.get(f"api/runs/{args.run_id}")
        return run if args.full else run_summary(run)
    if args.command == "resume":
        return client.post(
            f"api/runs/{args.run_id}/resume",
            {"decision": args.decision, "comment": args.comment},
        )
    project = resolve_project(client, args.project)
    if args.command == "runs":
        return {"project": project, "runs": list_runs(
            client, project["id"], args.production_line, args.include_archived
        )}
    if args.command == "create-run":
        return {"action": "created", "run": create_run(client, project, args)}
    active = [
        item for item in list_runs(client, project["id"], args.production_line)
        if item.get("status") not in TERMINAL_STATUSES
    ]
    if len(active) > 1:
        raise TaskHubClientError(
            "multiple active runs match this project and production line; use status with a run id"
        )
    if active:
        return {"action": "recovered", "project": project, "run": active[0]}
    return {"action": "created", "project": project, "run": create_run(client, project, args)}


def main() -> int:
    args = parser().parse_args()
    try:
        client = TaskHubClient.from_file(
            Path(args.connection_file).expanduser(), args.timeout_seconds
        )
        result = execute(client, args)
    except (OSError, ValueError, json.JSONDecodeError, TaskHubClientError, URLError) as error:
        print(f"TaskHub project client failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
