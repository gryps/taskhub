"""Real browser + HTTP + PostgreSQL acceptance; no mocked browser API responses.

Opt in with TASKHUB_TEST_BROWSER=1 and TASKHUB_TEST_POSTGRES_DSN.
Missing dependencies/configuration fail (rather than skip) when opted in.
"""
import importlib
import os
import socket
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
import uvicorn
from cryptography.fernet import Fernet

from taskhub_v2.config import Settings
from tests.fakes import RecordingProvider, RecordingWorker
from tests.test_workflow import RecoveringPublisher

pytestmark = pytest.mark.skipif(
    os.getenv("TASKHUB_TEST_BROWSER") != "1", reason="Browser acceptance is opt-in"
)


@contextmanager
def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                pytest.fail("Acceptance HTTP server did not start")
            time.sleep(0.05)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive(), "Acceptance server did not stop"


def test_browser_history_and_publication_recovery(monkeypatch, tmp_path, postgres_dsn):
    from playwright.sync_api import expect, sync_playwright

    app_module = importlib.import_module("taskhub_v2.api.app")
    provider, worker, publisher = RecordingProvider(), RecordingWorker(), RecoveringPublisher()
    # Only external execution adapters are deterministic; routes, graph, checkpoints,
    # index, authentication, SSE and all browser fetches are real.
    monkeypatch.setattr(app_module, "build_provider", lambda *args: provider)
    monkeypatch.setattr(app_module, "build_worker", lambda *args: worker)
    monkeypatch.setattr(app_module, "build_publisher", lambda *args: publisher)
    settings = Settings(
        checkpointer="postgres", postgres_dsn=postgres_dsn,
        admin_token="acceptance-only", session_secret="acceptance-only-session",
        config_encryption_key=Fernet.generate_key().decode(),
        projects_file=str(tmp_path / "projects.json"),
        provider_health_file=str(tmp_path / "health.json"),
        nodes_file=str(tmp_path / "nodes.json"),
        node_state_file=str(tmp_path / "nodes-state.json"),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    project = f"browser-{uuid4().hex}"
    errors = []
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    def login(browser):
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.locator("#admin-token").fill("acceptance-only")
        page.locator("#login-button").click()
        expect(page.locator("#workspace")).to_be_visible()
        page.wait_for_load_state("networkidle")
        page.locator("#nav-tasks").click()
        expect(page.locator("#task-center")).to_be_visible()
        expect(page.locator("#workflow-page")).to_be_hidden()
        page.locator("#filter-project").fill(project)
        return context, page

    def rows(page, count):
        expect(page.locator("#task-rows tr")).to_have_count(count)

    def sidebar(page):
        menu = page.locator(".side-menu").bounding_box()
        content = page.locator("#task-center").bounding_box()
        assert menu["x"] + menu["width"] <= content["x"]
        first = page.locator("#nav-tasks").bounding_box()
        second = page.locator("#nav-workflow").bounding_box()
        assert first["y"] + first["height"] <= second["y"]

    def action(page, stage, label):
        panel = page.locator(f'#action[data-stage="{stage}"]')
        expect(panel).to_be_visible()
        button = panel.locator("#approve")
        expect(button).to_be_visible()
        expect(button).to_have_text(label)
        button.click()

    with sync_playwright() as playwright:
        with serve(app_module.create_app(settings), port):
            browser = playwright.chromium.launch(executable_path=executable)
            context, page = login(browser)
            csrf = next(c["value"] for c in context.cookies() if c["name"] == "taskhub_v2_csrf")
            ids = []
            for n, line in enumerate(("A", "B", "A")):
                response = context.request.post(
                    f"{url}/api/runs", headers={"X-CSRF-Token": csrf},
                    data={"project_id": project, "requirement": f"Browser feature {n}",
                          "production_line": line},
                )
                assert response.status == 201
                ids.append(response.json()["run_id"])
            page.locator("#refresh-tasks").click()
            rows(page, 3)
            expect(page.locator("#exception-items .exception-item")).to_have_count(1)
            expect(page.locator("#exception-center-state")).to_have_text("1 项待处理")
            expect(page.locator("#exception-items")).to_contain_text("Git 发布")
            expect(page.locator("#evidence-items .evidence-item")).to_have_count(3)
            expect(page.locator("#evidence-items")).to_contain_text("制品完整性")
            sidebar(page)
            page.screenshot(path=str(tmp_path / "task-list.png"), full_page=True)
            page.reload()
            page.wait_for_load_state("networkidle")
            page.locator("#nav-tasks").click()
            page.locator("#filter-project").fill(project)
            rows(page, 3)
            page.locator("#filter-line").fill("B")
            rows(page, 1)
            expect(page.locator("#task-rows")).to_contain_text(ids[1])
            page.locator("#filter-line").fill("A")
            rows(page, 2)
            page.locator("#filter-status").select_option("blocked")
            rows(page, 1)
            page.locator("#filter-stage").select_option("merging")
            rows(page, 1)
            page.locator(f'tr[data-run-id="{ids[0]}"]').click()
            expect(page.locator("#flow .step")).to_have_count(9)
            expect(page.locator(".composer")).to_be_hidden()
            expect(page.locator("#status")).to_have_text("已阻塞")
            expect(page.locator('[data-step-id="merging"]')).to_have_class("step blocked")
            expect(page.locator("#action-detail")).to_have_text("temporary publication failure")
            page.screenshot(path=str(tmp_path / "task-blocked.png"), full_page=True)
            timeline = context.request.get(f"{url}/api/runs/{ids[0]}").json()["timeline"]
            context.close()
            browser.close()

        # A new browser context has no cookies/localStorage and the fresh application
        # lifespan must backfill its task index from the server-owned checkpoints.
        with serve(app_module.create_app(settings), port):
            browser = playwright.chromium.launch(executable_path=executable)
            context, page = login(browser)
            rows(page, 3)
            sidebar(page)
            page.set_viewport_size({"width": 680, "height": 900})
            sidebar(page)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.set_viewport_size({"width": 1440, "height": 1000})
            expect(page.locator(f'tr[data-run-id="{ids[0]}"]')).to_contain_text(
                "temporary publication failure"
            )
            page.locator(f'tr[data-run-id="{ids[0]}"]').click()
            action(page, "merging", "重新检查并发布")
            expect(page.locator("#status")).to_have_text("已完成")
            expect(page.locator("#flow .step.done")).to_have_count(9)
            expect(page.locator("#approve")).to_be_hidden()
            completed = context.request.get(f"{url}/api/runs/{ids[0]}").json()
            assert completed["timeline"][:len(timeline)] == timeline
            assert worker.calls == provider.review_calls == provider.supervisor_calls == 3
            assert provider.plan_calls == 3 and publisher.calls == 4
            page.screenshot(path=str(tmp_path / "task-recovered.png"), full_page=True)
            page.locator("#nav-tasks").click()
            rows(page, 3)
            expect(page.locator(f'tr[data-run-id="{ids[0]}"]')).to_contain_text("已完成")
            context.close()
            browser.close()
        assert not errors, errors
