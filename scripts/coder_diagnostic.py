#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

from taskhub_v2.config import get_settings
from taskhub_v2.domain.models import Plan
from taskhub_v2.providers.codex_account import CodexAccountError
from taskhub_v2.workers.factory import build_worker


async def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: coder_diagnostic.py RUN_ID PROJECT_ID")
    worker = build_worker(get_settings().model_copy(update={"worker_mode": "git"}))
    project = worker.projects.get(sys.argv[2])
    workspace = await worker.workspaces.prepare(project, sys.argv[1])
    plan = Plan(
        summary="Add subtract with a unit test",
        steps=["Add subtract", "Add test", "Run unittest"],
        acceptance=["All unit tests pass"],
    )
    for provider in worker.coder.providers:
        try:
            result = await provider.modify_workspace(
                "Add subtract(left, right) and its unit test without changing add.",
                plan,
                workspace.path,
            )
            print(f"{provider.provider_id}: ok model={result.model}")
            return
        except CodexAccountError as exc:
            print(f"{provider.provider_id}: {exc.reason}")
            if exc.diagnostic:
                Path(f"/tmp/taskhub-{provider.provider_id}-diagnostic.log").write_text(
                    exc.diagnostic, encoding="utf-8"
                )
                print(f"diagnostic=/tmp/taskhub-{provider.provider_id}-diagnostic.log")


if __name__ == "__main__":
    asyncio.run(main())
