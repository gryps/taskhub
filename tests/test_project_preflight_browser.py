"""Real-browser coverage for the project preflight center."""

import importlib
import os
import socket

import pytest

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ProjectDefinition
from tests.test_projects import add_remote, repository
from tests.test_task_center_browser import serve
from tests.test_workflow import RecoveringPublisher

pytestmark = pytest.mark.skipif(
    os.getenv("TASKHUB_TEST_BROWSER") != "1", reason="Browser acceptance is opt-in"
)


@pytest.mark.parametrize("width", [1440, 390])
def test_project_preflight_is_actionable_and_responsive(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    repo = repository(tmp_path / "shop")
    add_remote(repo, tmp_path / "shop.git")
    settings = Settings(
        admin_token="preflight-browser-token",
        session_secret="preflight-browser-session",
        projects_file=str(tmp_path / "projects.json"),
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    app = create_app(settings)
    app.state.projects.add(
        ProjectDefinition(
            id="shop",
            name="Shop",
            repository=str(repo),
            authority_remote="origin",
            test_commands=[["python3", "-c", "pass"]],
        )
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with sync_playwright() as playwright, serve(app, port):
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        page.locator("#admin-token").fill("preflight-browser-token")
        page.locator("#login-button").click()
        page.locator("#nav-workflow").click()

        expect(page.locator("#project-preflight")).to_be_visible()
        expect(page.locator("#project-preflight-state")).to_have_text("可以启动")
        expect(page.locator(".project-preflight-check")).to_have_count(6)
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#project-preflight-checks")).to_contain_text("Seed 执行能力")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_attached_project_quality_can_be_repaired_in_place(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    repo = repository(tmp_path / "shop")
    add_remote(repo, tmp_path / "shop.git")
    settings = Settings(
        admin_token="quality-browser-token",
        session_secret="quality-browser-session",
        projects_file=str(tmp_path / "projects.json"),
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    app = create_app(settings)
    app.state.projects.add(
        ProjectDefinition(
            id="shop",
            name="Shop",
            repository=str(repo),
            authority_remote="origin",
        )
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with sync_playwright() as playwright, serve(app, port):
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
        page.locator("#admin-token").fill("quality-browser-token")
        page.locator("#login-button").click()
        page.locator("#onboarding-later").click()
        page.locator("#nav-workflow").click()

        expect(page.locator("#project-preflight-state")).to_have_text("1 项阻塞")
        page.locator("#project-repository-disclosure > summary").click()
        expect(page.locator("#project-repository-disclosure")).to_contain_text(
            "项目授权节点 ID（启用时必填）"
        )
        page.locator("#project-quality-tests").fill("python3 -m pytest -q")
        page.locator("#project-quality-timeout").fill("1800")
        page.locator("#project-quality-windows-commands").fill(
            "powershell -File scripts/windows-acceptance.ps1"
        )
        page.locator("#save-project-quality").click()
        expect(page.locator("#project-quality-message")).to_have_text(
            "启用 Windows 实机测试时，必须填写当前项目获授权的节点 ID"
        )
        assert page.evaluate("document.activeElement.id") == "project-quality-windows-nodes"
        page.locator("#project-quality-windows-commands").fill("")
        page.locator("#save-project-quality").click()
        expect(page.locator("#project-quality-message")).to_have_text(
            "质量配置已保存，项目预检已刷新"
        )
        expect(page.locator("#project-preflight-state")).to_have_text("可以启动")
        expect(page.locator("#project-quality-timeout")).to_have_value("1800")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()


def test_exception_center_opens_the_checkpoint_owned_recovery_action(monkeypatch, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    app_module = importlib.import_module("taskhub_v2.api.app")
    publisher = RecoveringPublisher()
    monkeypatch.setattr(app_module, "build_publisher", lambda *args: publisher)
    settings = Settings(
        admin_token="exception-browser-token",
        session_secret="exception-browser-session",
        projects_file=str(tmp_path / "projects.json"),
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with sync_playwright() as playwright, serve(app_module.create_app(settings), port):
        browser = playwright.chromium.launch(executable_path=executable)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        page.locator("#admin-token").fill("exception-browser-token")
        page.locator("#login-button").click()
        csrf = next(
            cookie["value"]
            for cookie in context.cookies()
            if cookie["name"] == "taskhub_v2_csrf"
        )
        created = context.request.post(
            f"{url}/api/runs",
            headers={"X-CSRF-Token": csrf},
            data={"project_id": "demo", "requirement": "Verify exception recovery"},
        )
        assert created.status == 201
        assert created.json()["status"] == "blocked"
        page.locator("#nav-tasks").click()
        page.locator("#refresh-tasks").click()

        expect(page.locator("#exception-items .exception-item")).to_have_count(1)
        expect(page.locator("#exception-center-state")).to_have_text("1 项待处理")
        expect(page.locator("#exception-items")).to_contain_text("Git 发布")
        expect(page.locator("#exception-items")).to_contain_text("重试当前环节")
        expect(page.locator("#evidence-items .evidence-item")).to_have_count(1)
        expect(page.locator("#evidence-items")).to_contain_text("流程完整度")
        expect(page.locator("#evidence-items")).to_contain_text("来源链路")
        page.locator("#exception-items .exception-item button").click()
        expect(page.locator("#status")).to_have_text("已阻塞")
        expect(page.locator("#approve")).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        context.close()
        browser.close()
