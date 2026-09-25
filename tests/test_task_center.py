import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ResumeRequest, RunStatus, StartRunRequest
from taskhub_v2.persistence.task_index import MemoryTaskIndex
from taskhub_v2.services.runs import RunConflictError, RunService
from taskhub_v2.services.task_state import checkpoint_values, workflow_steps
from taskhub_v2.workflows import build_main_graph
from tests.fakes import RecordingProvider, RecordingWorker
from tests.test_workflow import RecoveringPublisher, RecoveringWorker


def settings():
    return Settings(
        checkpointer="memory", admin_token="admin-secret", session_secret="session-secret"
    )


def login(client):
    client.post("/api/auth/login", json={"token": "admin-secret"})
    return {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf")}


def wait_for_run(client, run_id, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if predicate(run):
            return run
        time.sleep(0.01)
    return client.get(f"/api/runs/{run_id}").json()


def test_memory_task_index_filters_and_preserves_created_time():
    async def exercise():
        index = MemoryTaskIndex()
        first = await index.upsert(
            {
                "run_id": "one",
                "requirement": "First task",
                "project_id": "demo",
                "current_stage": "planning",
                "status": "running",
            },
            "A",
        )
        await index.upsert(
            {
                "run_id": "one",
                "requirement": "First task",
                "project_id": "demo",
                "current_stage": "plan_approval",
                "status": "waiting",
                "pending_action": {"type": "plan_approval", "choices": ["approve", "reject"]},
            }
        )
        page = await index.list(production_line="A", status="waiting")
        assert page.total == 1
        assert page.items[0].created_at == first.created_at
        assert page.items[0].updated_at >= first.updated_at

        await index.upsert(
            {
                "run_id": "blocked",
                "requirement": "Blocked publication",
                "project_id": "demo",
                "current_stage": "merge_blocked",
                "status": "blocked",
            }
        )
        assert (await index.list(stage="merging")).total == 1

    asyncio.run(exercise())


def test_memory_task_index_filters_by_effective_rebound_project():
    async def exercise():
        index = MemoryTaskIndex()
        await index.upsert(
            {
                "run_id": "rebound",
                "requirement": "Move task",
                "project_id": "old",
                "current_stage": "merge_blocked",
                "status": "blocked",
            }
        )
        await index.rebind("rebound", "new")
        assert (await index.list(project_id="old")).total == 0
        assert [item.run_id for item in (await index.list(project_id="new")).items] == ["rebound"]

    asyncio.run(exercise())


def test_archived_tasks_are_hidden_and_upsert_does_not_revive_them():
    async def exercise():
        index = MemoryTaskIndex()
        values = {
            "run_id": "archived",
            "requirement": "Old task",
            "project_id": "gone",
            "current_stage": "implementation_blocked",
            "status": "blocked",
        }
        await index.upsert(values)
        archived = await index.archive("archived")
        assert archived.archived_at is not None
        del index._items["archived"]  # simulate loss of the rebuildable index row
        await index.upsert(dict(values, status="running"))
        assert (await index.list()).total == 0
        visible = await index.list(include_archived=True)
        assert visible.total == 1
        assert visible.items[0].archived_at == archived.archived_at

    asyncio.run(exercise())


def test_task_center_lists_three_tasks_and_filters_production_lines():
    with TestClient(create_app(settings())) as client:
        headers = login(client)
        for number, line in enumerate(("A", "B", "A"), 1):
            response = client.post(
                "/api/runs",
                headers=headers,
                json={
                    "project_id": "demo",
                    "requirement": f"Build feature {number}",
                    "production_line": line,
                },
            )
            assert response.status_code == 201
            wait_for_run(
                client, response.json()["run_id"], lambda run: run["status"] == "completed"
            )
        page = client.get("/api/runs").json()
        assert page["total"] == 3
        filtered = client.get("/api/runs?production_line=A&status=completed").json()
        assert filtered["total"] == 2
        assert {item["production_line"] for item in filtered["items"]} == {"A"}
        assert all(item["pending_action"] is None for item in filtered["items"])


def test_task_detail_exposes_automatic_nine_stage_ui():
    with TestClient(create_app(settings())) as client:
        headers = login(client)
        run = client.post(
            "/api/runs",
            headers=headers,
            json={"project_id": "demo", "requirement": "Build task details"},
        ).json()
        run = wait_for_run(client, run["run_id"], lambda item: item["status"] == "completed")
        detail = client.get(f"/api/runs/{run['run_id']}").json()
        assert detail["pending_action"] is None
        assert len(detail["workflow_steps"]) == 9
        assert all(step["state"] == "completed" for step in detail["workflow_steps"])
        assert detail["created_at"] and detail["updated_at"]
        html = client.get("/").text
        assert html.count("任务中心") >= 1
        assert 'id="password-setup"' in html
        assert 'id="bootstrap-token"' in html
        assert 'id="new-admin-password"' in html
        assert 'id="confirm-admin-password"' in html
        assert 'id="nav-workflow"' in html and "开发流程" in html
        assert 'id="nav-resources"' in html and "系统配置" in html
        assert '<input id="production-line" type="hidden" value="default">' in html
        assert "<label>生产线" not in html
        assert 'class="composer-requirement"' in html
        assert 'class="composer-actions"' in html
        assert html.count('class="providers-section resource-disclosure') == 4
        assert 'id="hosts-disclosure"' not in html
        assert 'id="platform-disclosure"' in html
        assert 'id="container-form"' in html
        assert 'id="model-services-form"' in html
        assert 'id="add-model-card"' in html
        assert 'id="model-config-audit"' in html
        assert 'id="platform-settings-form"' in html
        assert 'id="platform-config-audit"' in html
        assert "styles.css?v=62" in html
        assert 'href="/canvas/"' in html and "打开生产画布" in html
        assert "app.js?v=31" in html
        assert 'id="exception-center"' in html
        assert 'id="evidence-center"' in html
        assert "task-center.js?v=15" in html
        assert "revision-center.js?v=1" in html
        assert "capability-center.js?v=1" in html
        assert html.count('class="resource-disclosure-heading"') == 4
        assert html.count('class="resource-order"') == 4
        assert 'id="model-operations"' in html
        assert "resource-center.js?v=40" in html
        assert html.count('class="configuration-card"') >= 6
        assert html.count('class="management-card-grid"') >= 3
        assert 'id="login-username"' in html
        assert 'id="access-security-disclosure"' in html
        assert 'id="user-form"' in html
        assert 'id="host-rebuild-form"' not in html
        assert 'id="diagnostic-node"' in html
        assert 'id="export-diagnostics"' in html
        assert 'id="node-upgrade-form"' not in html
        assert 'id="container-target"' not in html
        assert "Seed 备份与恢复" in html
        assert "onboarding.js?v=3" in html
        assert 'id="collapse-current-resource"' in html
        assert 'class="resource-subdisclosure"' in html
        assert 'id="platform-registry-username"' in html
        assert 'id="platform-registry-password"' in html
        assert html.count("data-public-image-downloads") == 1
        assert all(
            f'id="{name}-disclosure"' in html
            for name in ("system", "providers", "nodes", "platform")
        )
        assert "<strong>Seed 状态</strong><small>控制器、持久化与运行就绪</small>" in html
        assert "<strong>主机池</strong>" not in html
        assert "<strong>TaskHub 节点</strong><small>Seed 本机 Docker 运行角色</small>" in html
        assert "<strong>模型服务</strong><small>高级 · 认证、角色路由与主备切换</small>" in html
        assert "<strong>高级设置</strong><small>镜像策略、安全、备份与平台参数</small>" in html
        assert html.count('data-resource-target=') == 0
        assert "<strong>预生产验收</strong><small>按项目启用的访问与验收环境</small>" in html
        assert "<strong>代码仓库</strong><small>项目级 Git 来源、基准分支与发布目标</small>" in html
        assert "<strong>产品规格</strong><small>需求产品化、待决策事项与批准版本</small>" in html
        assert "<strong>项目契约</strong><small>目录架构、质量命令与交付物门禁</small>" in html
        assert 'id="run-project-contract-gate"' in html
        assert 'id="product-decision-form"' in html
        assert 'id="approve-product-spec"' in html
        assert 'id="execution-plan-disclosure"' in html
        assert 'id="execution-plan-facts"' in html
        assert 'id="execution-analysis"' in html
        assert 'id="execution-task-pagination"' in html
        assert 'id="scheduling-policy-form"' in html
        assert 'id="execution-batches"' in html
        assert 'id="execution-tasks"' in html
        assert 'id="capability-disclosure"' in html
        assert 'id="capability-inventory-disclosure"' in html
        assert "/static/app.js?v=31" in html
        assert 'id="project-remote-url" required' in html
        assert 'id="project-local-path" required' in html
        assert 'id="attach-project-remote-url" required' in html
        assert 'id="attach-project-local-path" required' in html
        assert 'id="project-git-service"' in html
        assert 'id="attach-project-git-service"' in html
        assert 'id="platform-git-service-card"' in html
        assert 'id="platform-git-host" required' in html
        assert 'id="platform-git-private-key"' in html
        assert 'id="test-git-service"' in html
        assert 'id="project-repository-form"' in html
        assert 'id="check-project-repository"' in html
        assert 'id="save-project-repository"' in html
        assert 'id="project-quality-tests"' in html
        assert 'id="project-quality-windows-commands"' in html
        assert 'id="project-quality-windows-nodes"' in html
        assert "项目授权节点 ID（启用时必填）" in html
        assert 'id="project-quality-windows-artifacts"' in html
        assert 'id="save-project-quality"' in html
        assert 'id="workflow-project"' in html
        app_script = client.get("/static/app.js").text
        assert "projectProvisioningDefaults" in app_script
        assert "responseText ? JSON.parse(responseText)" in app_script
        assert 'remote_url: byId("project-remote-url")' in app_script
        assert 'local_path: byId("attach-project-local-path")' in app_script
        assert "openGitServiceSettings" in app_script
        assert "必须填写当前项目获授权的节点 ID" in app_script
        resource_script = client.get("/static/resource-center.js").text
        assert "/api/remote-nodes" not in resource_script
        assert "prepare-node-upgrade" not in resource_script
        assert "打开 OpenAI 官方登录页" in resource_script
        assert "等待 Codex CLI 返回登录地址和验证码" in resource_script
        assert "生成新的登录验证码" in resource_script
        assert "授权目标：Seed 控制器" in resource_script
        assert "五个角色各需一个主模型；其余卡片可设为备用" in resource_script
        assert "validateEnabledModelRoutes" in resource_script
        assert "测试并读取模型" in resource_script
        assert "正在测试当前卡片并读取模型列表" in resource_script
        assert "provider_id: draft.model_id, draft:" in resource_script
        assert '"/api/settings/platform/git-test"' in resource_script
        assert "model_id: draft.model_id, draft:" in resource_script
        assert "modelCardCredentials[draft.model_id]" in resource_script
        assert "model-catalog-select" in resource_script
        assert 'class="model-card-lower"' in resource_script
        assert 'class="model-card-action-buttons"' in resource_script
        assert "可用模型（" in resource_script
        assert 'class="secondary copy-device-code"' in resource_script
        assert "复制设备验证码" in resource_script
        assert 'button.textContent = "已复制"' in resource_script
        assert 'title: "执行节点"' in resource_script
        assert 'title: "测试节点"' in resource_script
        assert 'title: "预生产节点"' in resource_script
        assert '"workspace_write_sandbox", "工作区写入沙箱"' in resource_script
        assert "runtime-role-grid" in resource_script
        assert html.index('id="test-environment-disclosure"') < html.index('id="resource-page"')
        assert 'id="load-disclosure"' not in html
        assert 'id="containers-disclosure"' not in html
        assert 'id="acceptance-prerequisites-disclosure"' not in html
        assert 'id="test-environment-form"' in html
        assert 'id="check-test-environment"' in html
        assert 'id="edit-test-environment"' in html
        assert "启用预生产环境验收" in html
        assert "预生产访问地址" in html
        assert "入口网关主机（可选）" in html
        assert "应用服务主机（可选）" in html
        assert 'id="revise"' in html and "退回实施" in html
        assert 'id="manual"' in html and "平台处置" in html
        assert 'id="configure-resources"' in html and "更换节点/模型" in html
        assert 'id="add-evidence"' in html and "补充证据" in html
        assert html.count('id="archive-task"') == 1
        assert "<details><summary>规划方案" in html
        script = client.get("/static/app.js").text
        assert "copy-image-reference" in script
        assert "/repository/check" in script
        assert "/quality" in script
        assert "renderProjectRepository(active)" in script
        assert 'path = "/api/auth/setup"' in script
        assert 'localStorage.setItem("taskhub_run_id"' not in script
        assert 'decision === "revise" ? "revise"' in script
        assert 'decision === "manual" ? "manual"' in script
        assert 'byId("test-environment-enabled").checked' in resource_script
        assert '["completed", "rejected", "failed"].includes(run.status)' in script
        for stage in (
            "intake",
            "planning",
            "implementation",
            "acceptance",
            "review",
            "risk",
            "supervision",
            "merging",
            "completed",
        ):
            assert f'"{stage}"' in script
        assert 'id: "plan_approval"' not in script
        assert 'id: "merge_approval"' not in script


def test_task_center_publication_recovery_does_not_repeat_completed_work():
    app = create_app(settings())
    with TestClient(app) as client:
        provider, worker, publisher = RecordingProvider(), RecordingWorker(), RecoveringPublisher()
        app.state.run_service = RunService(
            build_main_graph(provider, worker, InMemorySaver(), publisher),
            task_index=MemoryTaskIndex(),
        )
        headers = login(client)
        run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "project_id": "demo",
                "requirement": "Publish with recovery",
                "production_line": "A",
            },
        ).json()
        run_id = client.get("/api/runs").json()["items"][0]["run_id"]
        assert run_id == run["run_id"]
        base = f"/api/runs/{run_id}"
        blocked = wait_for_run(client, run_id, lambda item: bool(item["blocking_reason"]))
        assert blocked["blocking_reason"]["detail"] == "temporary publication failure"
        assert blocked["pending_action"]["choices"] == ["retry", "revise", "cancel"]
        assert blocked["workflow_steps"][7]["state"] == "blocked"
        assert client.get("/api/runs?status=blocked").json()["total"] == 1
        assert (
            client.post(base + "/resume", headers=headers, json={"decision": "approve"}).status_code
            == 409
        )
        resumed = client.post(
            base + "/resume", headers=headers, json={"decision": "retry"}
        )
        assert resumed.status_code == 200
        completed = wait_for_run(client, run_id, lambda item: item["status"] == "completed")
        assert completed["status"] == "completed"
        assert provider.plan_calls == provider.review_calls == provider.risk_calls == 1
        assert provider.supervisor_calls == worker.calls == 1
        assert publisher.calls == 2
        assert all(e["title"] != "Plan approved" for e in completed["timeline"])
        assert all(e["title"] != "Publication approved" for e in completed["timeline"])


def test_running_checkpoint_can_replay_current_stage():
    async def scenario():
        class ReplayGraph:
            def __init__(self):
                self.next = ["acceptance"]
                self.values = {
                    "run_id": "run-replay",
                    "project_id": "demo",
                    "production_line": "A",
                    "requirement": "Replay stuck browser acceptance",
                    "current_stage": "browser_acceptance",
                    "status": "running",
                    "pending_action": None,
                    "blocking_reason": None,
                    "timeline": [],
                    "model_runs": [],
                }
                self.payloads = []

            async def aget_state(self, _config):
                return SimpleNamespace(values=self.values, next=self.next, tasks=[])

            async def astream(self, payload, _config, stream_mode):
                self.payloads.append(payload)
                yield "debug", {"type": "task", "payload": {"name": "acceptance"}}
                self.values = {
                    **self.values,
                    "current_stage": "completed",
                    "status": "completed",
                    "timeline": [
                        {
                            "stage": "completed",
                            "title": "Replay completed",
                            "detail": "",
                            "actor": "system",
                            "status": "completed",
                        }
                    ],
                }
                self.next = []
                yield "values", self.values

        graph = ReplayGraph()
        service = RunService(graph, task_index=MemoryTaskIndex())
        replayed = await service.replay("run-replay")

        assert graph.payloads == [None]
        assert replayed.status == RunStatus.COMPLETED
        assert replayed.stage == "completed"

    asyncio.run(scenario())


def test_replay_rejects_tasks_waiting_for_owner_action():
    async def scenario():
        service = RunService(
            build_main_graph(
                RecordingProvider(), RecordingWorker(), InMemorySaver(), RecoveringPublisher()
            ),
            task_index=MemoryTaskIndex(),
        )
        waiting = await service.start(StartRunRequest(project_id="demo", requirement="wait"))

        with pytest.raises(RunConflictError, match="only running tasks|explicit owner action"):
            await service.replay(waiting.run_id)

    asyncio.run(scenario())


def test_same_run_actions_are_serialized():
    async def scenario():
        service = RunService(graph=None)
        active = 0
        peak = 0

        async def fake_resume(run_id, request):
            nonlocal active, peak
            assert request.decision == "retry"
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return run_id

        service._resume = fake_resume
        results = await asyncio.gather(
            service.resume("same-run", ResumeRequest(decision="retry")),
            service.resume("same-run", ResumeRequest(decision="retry")),
        )

        assert results == ["same-run", "same-run"]
        assert peak == 1

    asyncio.run(scenario())


def test_launch_resume_returns_before_slow_recovery_and_rejects_duplicates():
    async def scenario():
        service = RunService(
            build_main_graph(RecordingProvider(), RecoveringWorker(), InMemorySaver()),
            task_index=MemoryTaskIndex(),
        )
        blocked = await service.start(
            StartRunRequest(project_id="demo", requirement="Recover asynchronously")
        )
        entered, release = asyncio.Event(), asyncio.Event()
        execute = service._execute

        async def slow_execute(run_id, payload):
            entered.set()
            await release.wait()
            await execute(run_id, payload)

        service._execute = slow_execute
        returned = await service.launch_resume(
            blocked.run_id, ResumeRequest(decision="retry")
        )
        assert returned.status == RunStatus.BLOCKED
        await entered.wait()
        with pytest.raises(RunConflictError, match="active workflow action"):
            await service.launch_resume(blocked.run_id, ResumeRequest(decision="retry"))
        release.set()
        for _ in range(100):
            completed = await service.get(blocked.run_id)
            if completed.status == RunStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("background recovery did not complete")

    asyncio.run(scenario())


def test_live_index_failure_and_stale_backfill():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class PausedProvider(RecordingProvider):
            async def create_plan(self, requirement):
                entered.set()
                await release.wait()
                raise RuntimeError("planner unavailable")

        index, saver = MemoryTaskIndex(), InMemorySaver()
        service = RunService(
            build_main_graph(PausedProvider(), RecordingWorker(), saver), task_index=index
        )
        task = asyncio.create_task(
            service.start(
                StartRunRequest(
                    project_id="demo", requirement="Failure during planning", production_line="B"
                )
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        live = (await index.list()).items[0]
        assert live.status == "running" and live.stage == "planning"
        release.set()
        with pytest.raises(RuntimeError, match="planner unavailable"):
            await task
        failed = await service.get(live.run_id)
        assert failed.status == "failed"
        assert "planner unavailable" in failed.blocking_reason["detail"]
        assert failed.pending_action is None
        assert failed.workflow_steps[1]["state"] == "blocked"
        assert failed.workflow_steps[2]["state"] == "not_started"
        assert (await index.get(live.run_id)).status == "failed"
        await index.upsert(
            dict(
                run_id=live.run_id,
                project_id="demo",
                requirement="stale",
                current_stage="intake",
                status="running",
            )
        )
        await service.backfill(saver)
        repaired = await index.get(live.run_id)
        assert repaired.status == "failed" and repaired.production_line == "B"
        await service.backfill(saver)
        assert (await index.get(live.run_id)).updated_at == repaired.updated_at

    asyncio.run(scenario())


def test_terminal_steps_and_pagination():
    for stage in ("failed", "rejected"):
        steps = workflow_steps(
            dict(current_stage=stage, status=stage, timeline=[{"stage": "implementation"}])
        )
        assert steps[2]["state"] == "blocked"
        assert all(step["state"] == "not_started" for step in steps[3:])
    assert all(
        step["state"] == "not_started"
        for step in workflow_steps(dict(current_stage="failed", status="failed"))
    )

    async def scenario():
        index = MemoryTaskIndex()
        for number in range(55):
            await index.upsert(
                dict(
                    run_id=str(number),
                    project_id="demo",
                    requirement="task",
                    current_stage="planning",
                    status="running",
                )
            )
        first, second = await index.list(), await index.list(page=2)
        assert len(first.items) == 50 and len(second.items) == 5
        assert len({item.run_id for item in first.items + second.items}) == 55

    asyncio.run(scenario())


def test_legacy_string_implementation_is_projected_as_execution_result():
    snapshot = SimpleNamespace(
        values={"implementation": "worker=local legacy result"}, tasks=[], next=[]
    )

    values = checkpoint_values(snapshot)

    assert values["implementation"].summary == "worker=local legacy result"
