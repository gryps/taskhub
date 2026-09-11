import asyncio
import json
import os
import time
from collections import defaultdict
from pathlib import Path

from taskhub_v2.domain.models import NodeDefinition, Plan, ScheduledCoding, ScheduledTests
from taskhub_v2.execution.registry import NodeRegistry
from taskhub_v2.execution.runner import NodeExecutionError, NodeRunner


class LocalTestScheduler:
    def __init__(self):
        self.runner = NodeRunner("")
        self.node = NodeDefinition(id="controller-local", kind="local")

    async def run(
        self, job_id, sticky_key, commands, timeout, workdir, workload="test", **kwargs
    ) -> ScheduledTests:
        return await self.runner.run(self.node, job_id, commands, timeout, workdir, **kwargs)

    async def status(self) -> list[dict]:
        return [{**await self.runner.health(self.node), "slots": 1, "active": 0}]


class NodeScheduler:
    def __init__(
        self,
        registry: NodeRegistry,
        runner: NodeRunner,
        state_file: str,
        failure_cooldown_seconds: int = 30,
    ):
        self.registry = registry
        self.runner = runner
        self.state_file = Path(state_file)
        self.failure_cooldown_seconds = failure_cooldown_seconds
        self.active: dict[str, int] = defaultdict(int)
        self.failed_until: dict[str, float] = {}
        self.assignments = self._load_assignments()
        self.condition = asyncio.Condition()
        self.cursor = 0

    async def run(
        self,
        job_id: str,
        sticky_key: str,
        commands: list[list[str]],
        timeout: int,
        workdir: str,
        workload: str = "test",
        *,
        required_capabilities_override: set[str] | None = None,
        target_url: str = "",
        git_commit: str = "",
        artifact_paths: list[str] | None = None,
        execution_environment: dict[str, str] | None = None,
        eligible_node_ids: set[str] | None = None,
    ) -> ScheduledTests:
        required = required_capabilities(commands)
        if workload == "browser_acceptance":
            required.update(
                {
                    "windows_gui",
                    "playwright",
                    "screenshot",
                    "trace",
                    "browser_profile",
                    "browser_authenticated",
                }
            )
        required.update(required_capabilities_override or set())
        excluded: set[str] = set()
        failures = []
        while True:
            try:
                node = await self._acquire(
                    sticky_key, excluded, required, workload, eligible_node_ids
                )
            except NodeExecutionError:
                if failures:
                    raise NodeExecutionError("; ".join(failures)) from None
                raise
            try:
                if workload == "browser_acceptance":
                    return await self.runner.run(
                        node,
                        job_id,
                        commands,
                        timeout,
                        workdir,
                        required_capabilities=required,
                        target_url=target_url,
                        git_commit=git_commit,
                        artifact_paths=artifact_paths or [],
                        execution_environment=execution_environment or {},
                    )
                return await self.runner.run(
                    node,
                    job_id,
                    commands,
                    timeout,
                    workdir,
                    required_capabilities=required,
                    git_commit=git_commit,
                    artifact_paths=artifact_paths or [],
                    execution_environment=execution_environment or {},
                )
            except Exception as exc:
                failures.append(str(exc))
                excluded.add(node.id)
                self.failed_until[node.id] = time.time() + self.failure_cooldown_seconds
                self.assignments.pop(sticky_key, None)
                self._save_assignments()
            finally:
                await self._release(node.id)
            enabled = [
                node
                for node in self.registry.list()
                if node.enabled
                and (eligible_node_ids is None or node.id in eligible_node_ids)
            ]
            if not [node for node in enabled if node.id not in excluded]:
                raise NodeExecutionError("; ".join(failures))

    async def preflight_browser(self, commands: list[list[str]], capabilities: set[str]) -> None:
        """Check eligibility before creating a preview or uploading a workspace.

        Execution checks again when acquiring its slot because health can change.
        """
        required = (
            required_capabilities(commands)
            | capabilities
            | {
                "windows_gui",
                "playwright",
                "screenshot",
                "trace",
                "browser_profile",
                "browser_authenticated",
            }
        )
        nodes = [
            node
            for node in self.registry.list()
            if node.enabled
            and "browser_acceptance" in node.workloads
            and self.failed_until.get(node.id, 0) <= time.time()
        ]
        health = await asyncio.gather(*(self.runner.health(node) for node in nodes))
        online = [item for item in health if item.get("status") == "ok"]
        if not online:
            raise NodeExecutionError("Windows 验收节点离线")
        missing = [
            required
            - {name for name, available in item.get("capabilities", {}).items() if available}
            for item in online
        ]
        if all(missing):
            raise NodeExecutionError(
                "browser acceptance preflight failed: " + ", ".join(sorted(min(missing, key=len)))
            )

    async def status(self) -> list[dict]:
        nodes = [node for node in self.registry.list() if node.enabled]
        health = await asyncio.gather(*(self.runner.health(node) for node in nodes))
        return [
            {
                **item,
                "kind": node.kind,
                "slots": node.slots,
                "workloads": sorted(node.workloads),
                "priority": node.priority,
                "active": self.active[node.id],
                "cooldown_seconds": max(0, int(self.failed_until.get(node.id, 0) - time.time())),
            }
            for node, item in zip(nodes, health, strict=True)
        ]

    async def run_coding(
        self,
        job_id: str,
        sticky_key: str,
        requirement: str,
        plan: Plan,
        feedback: str,
        timeout: int,
        workdir: str,
        *,
        required_capabilities_override: set[str] | None = None,
        eligible_node_ids: set[str] | None = None,
    ) -> ScheduledCoding:
        excluded: set[str] = set()
        failures = []
        while True:
            required = {"coding"} | (required_capabilities_override or set())
            node = await self._acquire(
                sticky_key, excluded, required, "coding", eligible_node_ids
            )
            try:
                return await self.runner.run_coding(
                    node, job_id, requirement, plan, feedback, timeout, workdir
                )
            except Exception as exc:
                failures.append(str(exc))
                excluded.add(node.id)
                self.failed_until[node.id] = time.time() + self.failure_cooldown_seconds
                self.assignments.pop(sticky_key, None)
                self._save_assignments()
            finally:
                await self._release(node.id)
            enabled = [
                node
                for node in self.registry.list()
                if node.enabled
                and (eligible_node_ids is None or node.id in eligible_node_ids)
            ]
            if not [node for node in enabled if node.id not in excluded]:
                raise NodeExecutionError("; ".join(failures))

    async def _acquire(
        self,
        sticky_key: str,
        excluded: set[str],
        required: set[str],
        workload: str,
        eligible_node_ids: set[str] | None = None,
    ) -> NodeDefinition:
        while True:
            now = time.time()
            candidates = [
                node
                for node in self.registry.list()
                if node.enabled
                and (eligible_node_ids is None or node.id in eligible_node_ids)
                and node.id not in excluded
                and workload in node.workloads
                and self.failed_until.get(node.id, 0) <= now
            ]
            if not candidates:
                if workload == "browser_acceptance":
                    raise NodeExecutionError("Windows 验收节点离线")
                raise NodeExecutionError("no execution node is available")
            health = await asyncio.gather(*(self.runner.health(node) for node in candidates))
            nodes = [
                node
                for node, item in zip(candidates, health, strict=True)
                if item.get("status") == "ok"
                and required.issubset(
                    {name for name, available in item.get("capabilities", {}).items() if available}
                )
            ]
            if not nodes:
                if workload == "browser_acceptance" and not any(
                    item.get("status") == "ok" for item in health
                ):
                    raise NodeExecutionError("Windows 验收节点离线")
                needed = ", ".join(sorted(required)) or "basic execution"
                prefix = (
                    "browser acceptance preflight failed"
                    if workload == "browser_acceptance"
                    else "no healthy execution node provides"
                )
                raise NodeExecutionError(f"{prefix}: {needed}")
            async with self.condition:
                preferred = self.assignments.get(sticky_key)
                available = [node for node in nodes if self.active[node.id] < node.slots]
                preferred_node = next((node for node in available if node.id == preferred), None)
                selected = preferred_node
                if not selected and available:
                    best_priority = min(node.priority for node in available)
                    preferred_pool = [node for node in available if node.priority == best_priority]
                    minimum = min(self.active[node.id] / node.slots for node in preferred_pool)
                    tied = sorted(
                        (
                            node
                            for node in preferred_pool
                            if self.active[node.id] / node.slots == minimum
                        ),
                        key=lambda node: node.id,
                    )
                    selected = tied[self.cursor % len(tied)]
                    self.cursor += 1
                if selected:
                    self.active[selected.id] += 1
                    self.assignments[sticky_key] = selected.id
                    self._save_assignments()
                    return selected
                await self.condition.wait()

    async def _release(self, node_id: str) -> None:
        async with self.condition:
            self.active[node_id] = max(0, self.active[node_id] - 1)
            self.condition.notify_all()

    def _load_assignments(self) -> dict[str, str]:
        if not self.state_file.is_file():
            return {}
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            return dict(payload.get("assignments", {}))
        except (OSError, ValueError, TypeError):
            return {}

    def _save_assignments(self) -> None:
        self.state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"assignments": self.assignments}, indent=2), encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_file)


def required_capabilities(commands: list[list[str]]) -> set[str]:
    required: set[str] = set()
    for command in commands:
        if not command:
            continue
        executable = Path(command[0]).name
        if executable in {"python", "python3"}:
            required.add("python3")
            if len(command) > 2 and command[1:3] == ["-m", "pytest"]:
                required.add("pytest")
        elif executable in {"pytest", "py.test"}:
            required.add("pytest")
        elif executable in {"node", "npm", "npm.cmd", "git"}:
            required.add(executable.removesuffix(".cmd"))
        elif executable in {"npx", "npx.cmd"}:
            required.add("npm")
    return required
