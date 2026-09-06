#!/usr/bin/env python3
"""Serve a disposable TaskHub acceptance instance with deterministic adapters."""

import argparse
from pathlib import Path

import uvicorn

import taskhub_v2.api.app as app_module
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import (
    ExecutionResult,
    ModelResult,
    Plan,
    PublicationResult,
    SupervisionDecision,
)


class AcceptanceProvider:
    async def create_plan(self, requirement):
        plan = Plan(summary=f"Plan for {requirement}", steps=["change", "verify"], acceptance=["works"])
        return ModelResult(content=plan, provider="acceptance", model="deterministic")

    async def review(self, requirement, implementation):
        return ModelResult(content="accepted", provider="acceptance", model="deterministic")

    async def assess_risk(self, requirement, implementation):
        return ModelResult(content="low", provider="acceptance", model="deterministic")

    async def supervise(self, requirement, implementation, review, risk):
        decision = SupervisionDecision(decision="approve", summary="accepted", reasons=["tests pass"])
        return ModelResult(content=decision, provider="acceptance", model="deterministic")


class AcceptanceWorker:
    async def execute(self, run_id, project_id, requirement, plan, revision=0, feedback=""):
        return ExecutionResult(summary=f"artifact:{run_id}:{project_id}")


class RecoveringPublisher:
    def __init__(self):
        self.calls = 0

    async def publish(self, run_id, project_id, implementation):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary publication failure")
        return PublicationResult(
            project_id=project_id,
            authority_ref="main",
            previous_commit="before",
            published_commit="after",
            branch=f"taskhub/{run_id}",
        )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8324)
    return parser.parse_args()


def main():
    args = parse_args()
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    provider = AcceptanceProvider()
    worker = AcceptanceWorker()
    publisher = RecoveringPublisher()
    app_module.build_provider = lambda *unused: provider
    app_module.build_worker = lambda *unused: worker
    app_module.build_publisher = lambda *unused: publisher
    settings = Settings(
        checkpointer="postgres",
        postgres_dsn=args.dsn,
        admin_token="acceptance-only",
        session_secret="acceptance-only-session",
        projects_file=str(state_dir / "projects.json"),
        provider_health_file=str(state_dir / "health.json"),
        nodes_file=str(state_dir / "nodes.json"),
        node_state_file=str(state_dir / "nodes-state.json"),
    )
    uvicorn.run(app_module.create_app(settings), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
