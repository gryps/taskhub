"""Real-browser regression checks for Seed onboarding and system configuration."""

import json
import os
import socket
from pathlib import Path

import pytest

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from tests.test_task_center_browser import serve

pytestmark = pytest.mark.skipif(
    os.getenv("TASKHUB_TEST_BROWSER") != "1", reason="Browser acceptance is opt-in"
)


def test_onboarding_and_role_overview_layout(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    settings = Settings(
        checkpointer="memory",
        admin_token="browser-layout-token",
        session_secret="browser-layout-session",
        projects_file=str(tmp_path / "projects.json"),
        provider_secrets_file=str(tmp_path / "providers.env"),
        provider_health_file=str(tmp_path / "provider-health.json"),
        nodes_file=str(tmp_path / "nodes.json"),
        node_state_file=str(tmp_path / "node-state.json"),
        workspace_root=str(tmp_path / "workspaces"),
        artifact_root=str(tmp_path / "artifacts"),
    )
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "artifacts").mkdir()
    Path(settings.projects_file).write_text(
        json.dumps(
            {
                "projects": [
                    {"id": "alpha-project", "name": "Alpha 项目", "repository": "/tmp/alpha"},
                    {"id": "beta-project", "name": "Beta 项目", "repository": "/tmp/beta"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with sync_playwright() as playwright, serve(create_app(settings), port):
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        expect(page.locator("#login .public-image-downloads")).to_have_count(0)
        assert page.locator("#login").bounding_box()["width"] == 430
        page.locator("#admin-token").fill("browser-layout-token")
        page.locator("#login-button").click()

        expect(page.locator("#onboarding-page")).to_be_visible()
        expect(page.locator("#onboarding-steps .onboarding-step")).to_have_count(7)
        assert page.locator("#onboarding-message").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "17px"
        assert page.locator(".onboarding-step p").first.evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "12px"

        page.locator("#onboarding-later").click()
        page.locator("#nav-workflow").click()
        project_select = page.locator("#workflow-project")
        expect(project_select).to_be_enabled()
        expect(project_select.locator("option")).to_have_count(2)
        project_select.select_option("alpha-project")
        expect(project_select).to_have_value("alpha-project")
        project_select.select_option("beta-project")
        expect(project_select).to_have_value("beta-project")
        page.locator("#test-environment-disclosure > summary").click()
        assert page.locator(".project-settings-heading strong").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "15px"
        assert page.locator(".project-settings-heading small").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "12px"
        expect(page.locator(".field-help")).to_have_count(3)
        page.locator("#nav-resources").click()
        expect(page.locator(".resource-disclosure-heading")).to_have_count(5)
        expect(page.locator(".resource-order")).to_have_count(5)
        assert page.locator("#system-disclosure > summary").evaluate(
            "element => getComputedStyle(element).minHeight"
        ) == "68px"
        expect(page.locator("#resource-page .public-image-downloads")).to_be_visible()
        expect(page.locator("#resource-page .image-download-row")).to_have_count(4)
        expect(page.locator(".runtime-role-card")).to_have_count(4)
        expect(page.locator("#system-summary")).to_contain_text("角色环境")
        expect(page.locator("#nodes-disclosure summary").first).to_contain_text("工作节点")
        page.locator("#nodes-disclosure summary").first.click()
        expect(page.locator("#nodes.management-card-grid")).to_be_visible()
        expect(page.locator("#managed-containers.management-card-grid")).to_be_visible()
        upgrade = page.locator("#node-upgrade-disclosure")
        upgrade.locator("summary").click()
        expect(page.locator("#node-upgrade-form")).to_be_visible()
        assert page.locator(".node-upgrade-card h3").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "14px"
        assert page.locator(".node-upgrade-card .form-help").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "12px"
        diagnostics = page.locator("#node-diagnostics").locator("xpath=..")
        diagnostics.locator("summary").click()
        expect(page.locator("#load-node-diagnostics")).to_be_visible()
        expect(page.locator("#export-diagnostics")).to_be_visible()
        assert page.locator("#export-diagnostics").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "12px"
        assert page.locator("#export-diagnostics").evaluate(
            "element => getComputedStyle(element).textDecorationLine"
        ) == "none"
        page.locator("#platform-disclosure summary").first.click()
        expect(page.locator("#platform-settings .management-card")).to_have_count(4)
        expect(page.locator("#platform-settings-form .configuration-card")).to_have_count(4)
        expect(page.locator(".backup-contract")).to_be_attached()
        platform_form = page.locator("#platform-settings-form").locator("xpath=..")
        platform_form.locator("summary").click()
        assert page.locator(".backup-contract").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "13px"
        assert len(page.locator(".platform-address-fields").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns"
        ).split()) == 2
        page.locator("#access-security-disclosure summary").click()
        expect(page.locator("#user-inventory")).to_contain_text("admin")
        expect(page.locator("#user-form")).to_be_visible()
        expect(page.locator(".security-overview > div")).to_have_count(3)
        expect(page.locator("#session-identity")).to_contain_text("管理员")
        assert len(page.locator(".runtime-role-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns"
        ).split()) == 2

        for width, height in ((680, 900), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(100)
            assert page.evaluate("window.innerWidth") == width
            assert page.evaluate("window.matchMedia('(max-width: 680px)').matches")
            page.locator("#system-disclosure").evaluate("element => { element.open = true; }")
            page.locator("#platform-disclosure").evaluate("element => { element.open = true; }")
            assert len(page.locator(".runtime-role-grid").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ).split()) == 1
            address_columns = len(page.locator(".platform-address-fields").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ).split())
            assert address_columns == (2 if width == 680 else 1)
            page.locator("#nodes-disclosure").evaluate("element => { element.open = true; }")
            page.locator("#node-upgrade-disclosure").evaluate(
                "element => { element.open = true; }"
            )
            assert len(page.locator(".node-upgrade-card .configuration-card-fields").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ).split()) == 1
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            if width == 680:
                menu = page.locator(".side-menu").bounding_box()
                content = page.locator("#resource-page").bounding_box()
                assert menu["x"] + menu["width"] <= content["x"]
        assert not errors
        browser.close()
