import asyncio
import json

from taskhub_v2.domain.models import ScheduledTests
from taskhub_v2.execution.registry import NodeRegistry
from taskhub_v2.execution.runner import NodeExecutionError
from taskhub_v2.execution.scheduler import NodeScheduler


class RecordingRunner:
    def __init__(self, failing=(), capabilities=None):
        self.failing = set(failing)
        self.capabilities = capabilities or {}
        self.calls = []

    async def run(self, node, job_id, commands, timeout, workdir):
        self.calls.append((job_id, node.id))
        if node.id in self.failing:
            raise NodeExecutionError(f"{node.id} unavailable")
        return ScheduledTests(node_id=node.id, tests=[])

    async def health(self, node):
        return {
            "status": "ok",
            "node_id": node.id,
            "capabilities": self.capabilities.get(node.id, {}),
        }


def registry(tmp_path):
    path = tmp_path / "nodes.json"
    path.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "node-a", "kind": "local", "slots": 1},
                    {
                        "id": "node-b",
                        "kind": "remote",
                        "url": "http://node-b:8301",
                        "slots": 1,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return NodeRegistry(str(path))


def test_scheduler_round_robins_new_runs_and_keeps_sticky_assignment(tmp_path):
    async def scenario():
        runner = RecordingRunner()
        scheduler = NodeScheduler(
            registry(tmp_path), runner, str(tmp_path / "state.json")
        )
        first = await scheduler.run("job-1", "run-1", [], 30, str(tmp_path))
        second = await scheduler.run("job-2", "run-2", [], 30, str(tmp_path))
        repeated = await scheduler.run("job-3", "run-1", [], 30, str(tmp_path))
        return first, second, repeated

    first, second, repeated = asyncio.run(scenario())
    assert first.node_id == "node-a"
    assert second.node_id == "node-b"
    assert repeated.node_id == "node-a"
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600


def test_scheduler_fails_over_to_another_node(tmp_path):
    async def scenario():
        runner = RecordingRunner(failing={"node-a"})
        scheduler = NodeScheduler(
            registry(tmp_path), runner, str(tmp_path / "state.json")
        )
        return await scheduler.run("job-1", "run-1", [], 30, str(tmp_path)), runner

    result, runner = asyncio.run(scenario())
    assert result.node_id == "node-b"
    assert runner.calls == [("job-1", "node-a"), ("job-1", "node-b")]


def test_scheduler_routes_only_to_node_with_required_tool(tmp_path):
    async def scenario():
        runner = RecordingRunner(
            capabilities={
                "node-a": {"python3": True, "pytest": True, "npm": False},
                "node-b": {"python3": True, "pytest": True, "npm": True},
            }
        )
        scheduler = NodeScheduler(
            registry(tmp_path), runner, str(tmp_path / "state.json")
        )
        result = await scheduler.run(
            "job-npm", "run-npm", [["npm", "test"]], 30, str(tmp_path)
        )
        return result, runner

    result, runner = asyncio.run(scenario())
    assert result.node_id == "node-b"
    assert runner.calls == [("job-npm", "node-b")]


def test_scheduler_reports_missing_required_tool(tmp_path):
    async def scenario():
        runner = RecordingRunner(
            capabilities={
                "node-a": {"python3": True, "pytest": False},
                "node-b": {"python3": True, "pytest": False},
            }
        )
        scheduler = NodeScheduler(
            registry(tmp_path), runner, str(tmp_path / "state.json")
        )
        await scheduler.run(
            "job-pytest",
            "run-pytest",
            [["python3", "-m", "pytest", "-q"]],
            30,
            str(tmp_path),
        )

    import pytest

    with pytest.raises(NodeExecutionError, match="pytest, python3"):
        asyncio.run(scenario())


def test_scheduler_prefers_primary_and_uses_fallback_when_primary_fails(tmp_path):
    path = tmp_path / "nodes.json"
    path.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "primary", "kind": "local", "priority": 10},
                    {"id": "fallback", "kind": "local", "priority": 100},
                ]
            }
        ),
        encoding="utf-8",
    )

    async def scenario():
        runner = RecordingRunner(failing={"primary"})
        scheduler = NodeScheduler(
            NodeRegistry(str(path)), runner, str(tmp_path / "state.json")
        )
        result = await scheduler.run("job-1", "run-1", [], 30, str(tmp_path))
        return result, runner

    result, runner = asyncio.run(scenario())
    assert result.node_id == "fallback"
    assert runner.calls == [("job-1", "primary"), ("job-1", "fallback")]


def test_scheduler_filters_nodes_by_workload(tmp_path):
    path = tmp_path / "nodes.json"
    path.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "tests-only", "kind": "local", "workloads": ["test"]},
                    {"id": "coding", "kind": "local", "workloads": ["coding"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    async def scenario():
        runner = RecordingRunner()
        scheduler = NodeScheduler(
            NodeRegistry(str(path)), runner, str(tmp_path / "state.json")
        )
        return await scheduler.run(
            "job-code", "run-code", [], 30, str(tmp_path), workload="coding"
        )

    result = asyncio.run(scenario())
    assert result.node_id == "coding"


def test_browser_preflight_does_not_execute_or_reserve_slots(tmp_path):
    import pytest

    path = tmp_path / "nodes.json"
    path.write_text(json.dumps({"nodes": [{"id": "windows-gui-34", "kind": "remote",
        "url": "http://192.168.31.34:8301", "workloads": ["browser_acceptance"]}]}))
    capabilities = dict.fromkeys(["windows_gui", "playwright", "chromium", "edge",
                                  "screenshot", "video", "trace", "python3", "pytest"], True)
    runner = RecordingRunner(capabilities={"windows-gui-34": capabilities})
    scheduler = NodeScheduler(NodeRegistry(str(path)), runner, str(tmp_path / "state.json"))

    async def scenario():
        await scheduler.preflight_browser([["python3", "-m", "pytest"]], {"edge"})
        capabilities["edge"] = False
        with pytest.raises(NodeExecutionError, match="preflight failed: edge"):
            await scheduler.preflight_browser([], {"edge"})
        scheduler.failed_until["windows-gui-34"] = float("inf")
        with pytest.raises(NodeExecutionError, match="Windows 验收节点离线"):
            await scheduler.preflight_browser([], {"edge"})
        assert not runner.calls
        assert not scheduler.active
        assert not scheduler.assignments

    asyncio.run(scenario())
