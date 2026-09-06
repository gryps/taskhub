#!/usr/bin/env python3
"""Serve a disposable TaskHub acceptance instance with deterministic adapters."""

import argparse
import json
import os
from pathlib import Path

import uvicorn
from fastapi.responses import JSONResponse

import taskhub_v2.api.app as app_module
from taskhub_v2.artifacts import ArtifactStore
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
        rejected = "[acceptance:revision-limit]" in requirement
        decision = SupervisionDecision(
            decision="reject" if rejected else "approve",
            summary="acceptance control state" if rejected else "accepted",
            reasons=["exercise owner recovery actions"] if rejected else ["tests pass"],
        )
        return ModelResult(content=decision, provider="acceptance", model="deterministic")


class AcceptanceWorker:
    def __init__(self, artifact_root):
        self.artifacts = ArtifactStore(artifact_root)

    async def execute(self, run_id, project_id, requirement, plan, revision=0, feedback=""):
        artifact = self.artifacts.write_text(
            run_id,
            "candidate-report.html",
            "playwright_report",
            "<!doctype html><h1>Candidate artifact available</h1>",
        )
        return ExecutionResult(summary=f"artifact:{run_id}:{project_id}", artifacts=[artifact])


class TransientPublicationError(RuntimeError):
    retry_after_seconds = 2


class RecoveringPublisher:
    def __init__(self):
        self.calls = 0
        self.attempted = set()

    async def publish(self, run_id, project_id, implementation):
        self.calls += 1
        if run_id not in self.attempted:
            self.attempted.add(run_id)
            raise TransientPublicationError("temporary publication failure")
        return PublicationResult(
            project_id=project_id,
            authority_ref="main",
            previous_commit="before",
            published_commit="after",
            branch=f"taskhub/{run_id}",
        )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.getenv("TASKHUB_PREVIEW_DSN"))
    parser.add_argument("--state-dir", default=os.getenv("TASKHUB_PREVIEW_STATE_DIR"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8324)
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.dsn or not args.state_dir:
        raise SystemExit("preview DSN and state directory are required")
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "nodes.json").write_text(
        json.dumps({"nodes": [{
            "id": "windows-gui-34",
            "kind": "remote",
            "url": "http://192.168.31.34:8301",
            "slots": 1,
            "workloads": ["browser_acceptance"],
            "priority": 1,
            "enabled": True,
        }]}),
        encoding="utf-8",
    )
    settings = Settings(
        checkpointer="postgres",
        postgres_dsn=args.dsn,
        admin_token="acceptance-only",
        session_secret="acceptance-only-session",
        projects_file=str(state_dir / "projects.json"),
        provider_health_file=str(state_dir / "health.json"),
        nodes_file=str(state_dir / "nodes.json"),
        node_state_file=str(state_dir / "nodes-state.json"),
        artifact_root=str(state_dir / "artifacts"),
        node_token=os.getenv("TASKHUB_NODE_TOKEN", ""),
        test_runner="scheduled",
    )
    provider = AcceptanceProvider()
    worker = AcceptanceWorker(settings.artifact_root)
    publisher = RecoveringPublisher()
    app_module.build_provider = lambda *unused: provider
    app_module.build_worker = lambda *unused: worker
    app_module.build_publisher = lambda *unused: publisher
    app = app_module.create_app(settings)

    @app.middleware("http")
    async def candidate_identity(request, call_next):
        if request.url.path == "/api/health":
            return JSONResponse({"status": "ok", "git_commit": os.getenv("TASKHUB_GIT_COMMIT", "")})
        return await call_next(request)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
