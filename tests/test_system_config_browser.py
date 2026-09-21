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
        expect(page.locator("#onboarding-steps .onboarding-step")).to_have_count(6)
        assert page.locator("#onboarding-message").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "17px"
        assert page.locator(".onboarding-step p").first.evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "12px"

        page.locator("#onboarding-later").click()
        page.route(
            "**/api/projects/available",
            lambda route: route.fulfill(
                json={
                    "repositories": [
                        {
                            "repository": "existing.git",
                            "name": "Existing",
                            "default_branch": "main",
                            "attached": False,
                            "project_id": "existing",
                            "remote_url": "ssh://git@example.test/srv/git/existing.git",
                            "local_path": "/var/lib/taskhub/repositories/existing",
                        }
                    ],
                    "defaults": {
                        "authority_url_prefix": "ssh://git@example.test/srv/git",
                        "managed_repository_root": "/var/lib/taskhub/repositories",
                        "authority_service": "git@example.test:22 · /srv/git",
                    },
                    "discovery_error": None,
                }
            ),
        )
        page.locator("#nav-workflow").click()
        page.locator("#show-project-form").click()
        page.locator("#project-name").fill("New Project")
        expect(page.locator("#project-remote-url")).to_have_value(
            "ssh://git@example.test/srv/git/new-project.git"
        )
        expect(page.locator("#project-local-path")).to_have_value(
            "/var/lib/taskhub/repositories/new-project"
        )
        for width in (1440, 768, 390):
            page.set_viewport_size({"width": width, "height": 900})
            expect(page.locator("#project-remote-url")).to_be_visible()
            expect(page.locator("#project-local-path")).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator("#close-project-form").click()
        page.locator("#show-attach-project-form").click()
        expect(page.locator("#attach-project-remote-url")).to_have_value(
            "ssh://git@example.test/srv/git/existing.git"
        )
        expect(page.locator("#attach-project-local-path")).to_have_value(
            "/var/lib/taskhub/repositories/existing"
        )
        expect(page.locator("#attach-project-git-service")).to_have_text(
            "git@example.test:22 · /srv/git"
        )
        page.locator("#close-attach-project-form").click()
        project_select = page.locator("#workflow-project")
        expect(project_select).to_be_enabled()
        expect(project_select.locator("option")).to_have_count(2)
        project_select.select_option("alpha-project")
        expect(project_select).to_have_value("alpha-project")
        project_select.select_option("beta-project")
        expect(project_select).to_have_value("beta-project")
        page.locator("#project-repository-disclosure > summary").click()
        expect(page.locator("#project-repository-form")).to_be_visible()
        assert page.locator(".project-repository-card h3").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "14px"
        assert page.locator(".project-repository-facts dd").first.evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "13px"
        page.locator("#test-environment-disclosure > summary").click()
        environment_heading = page.locator(
            "#test-environment-disclosure .project-settings-heading"
        )
        assert environment_heading.locator("strong").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "15px"
        assert environment_heading.locator("small").evaluate(
            "element => getComputedStyle(element).fontSize"
        ) == "12px"
        expect(page.locator("#test-environment-form .field-help")).to_have_count(3)
        page.locator("#nav-resources").click()
        expect(page.locator(".resource-disclosure-heading")).to_have_count(4)
        expect(page.locator(".resource-order")).to_have_count(4)
        expect(page.locator(".system-setup-flow button")).to_have_count(2)
        expect(page.locator("#hosts-disclosure")).to_have_count(0)
        expect(page.locator("#nodes-disclosure > summary")).to_contain_text("TaskHub 节点")
        page.locator("#platform-disclosure").evaluate("element => { element.open = true; }")
        page.locator("#platform-config-disclosure").evaluate(
            "element => { element.open = true; }"
        )
        expect(page.locator("#platform-git-service-card")).to_be_visible()
        expect(page.locator("#platform-git-host")).to_have_value("gryps@192.168.31.3")
        expect(page.locator("#test-git-service")).to_be_visible()
        page.route(
            "**/api/providers/operations",
            lambda route: route.fulfill(json={
                "summary": {"providers": 2, "unhealthy_providers": 1, "invocations": 7,
                            "fallback_events": 1, "average_duration_ms": 900,
                            "sampled_runs": 3, "available_runs": 3},
                "providers": [{"id": "gpt_api", "kind": "api", "model": "gpt-test",
                               "configured": True, "operational_state": "cooldown",
                               "reason": "quota_exceeded", "failure_count": 3,
                               "recovery_count": 0, "retry_at": 1234,
                               "billing": {"status": "unavailable", "detail": "账单权限不可用",
                                           "metrics": []}}],
                "usage": [{"provider": "gpt_api", "model": "gpt-test", "invocations": 7,
                           "duration_ms": 6300, "fallback_events": 1,
                           "average_duration_ms": 900, "roles": {"coder": 7}}],
                "role_counts": {"coder": 7}, "role_models": {"coder": "gpt-test"},
            }),
        )
        page.locator("#providers-disclosure").evaluate("element => { element.open = true; }")
        page.evaluate("loadModelOperations()")
        expect(page.locator("#model-operations-summary")).to_have_text("7 次调用 · 1 次回退")
        expect(page.locator("#model-operations")).to_contain_text("熔断中")
        expect(page.locator("#model-operations")).to_contain_text("quota_exceeded")
        expect(page.locator("#provider-summary")).to_contain_text("已认证")
        captured_model_test = {}

        def fulfill_model_test(route):
            captured_model_test.update(route.request.post_data_json)
            route.fulfill(
                json={
                    "provider_id": "model-browser-check",
                    "available": True,
                    "detail": "读取到 2 个模型",
                    "models": [
                        {"id": "gpt-model-a", "name": "GPT Model A"},
                        {"id": "gpt-model-b", "name": "GPT Model B"},
                    ],
                }
            )

        page.route("**/api/settings/model-services/test", fulfill_model_test)
        page.evaluate("""
          () => {
            document.querySelector('#model-card-editor').innerHTML = modelCardHtml({
              model_id: 'model-browser-check',
              display_name: '浏览器模型',
              service_type: 'minimax',
              auth_mode: 'api',
              base_url: 'https://api.minimax.example/v1',
              model: '',
              proxy_url: '',
              enabled: true,
              assignments: [{role: 'planner', priority: 0}],
            });
            Object.defineProperty(navigator, 'clipboard', {
              configurable: true,
              value: {writeText: async (value) => { window.__copiedDeviceCode = value; }},
            });
            window.__routeValidation = validateEnabledModelRoutes([
              {enabled: true, assignments: [{role: 'planner', priority: 0}]},
              {enabled: true, assignments: [{role: 'coder', priority: 0}]},
            ]);
          }
        """)
        expect(page.locator(".model-role-help")).to_be_visible()
        assert "监督、评审、风险分析" in page.evaluate("window.__routeValidation")
        page.locator('[data-field="api_key"]').fill("browser-draft-secret")
        page.locator(".model-test").click()
        catalog = page.locator(".model-catalog-select")
        expect(catalog).to_be_visible()
        assert captured_model_test == {
            "provider_id": "model-browser-check",
            "draft": {
                "model_id": "model-browser-check",
                "service_type": "minimax",
                "auth_mode": "api",
                "base_url": "https://api.minimax.example/v1",
                "proxy_url": "",
                "api_key": "browser-draft-secret",
            },
        }
        expect(catalog.locator("option")).to_have_count(3)
        catalog.select_option("gpt-model-b")
        expect(page.locator('[data-field="model"]')).to_have_value("gpt-model-b")
        role_box = page.locator(".model-role-selector").bounding_box()
        action_box = page.locator(".model-card-actions").bounding_box()
        assert role_box["x"] < action_box["x"]
        for width in (768, 680, 390):
            page.set_viewport_size({"width": width, "height": 900})
            lower_columns = len(page.locator(".model-card-lower").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ).split())
            assert lower_columns == (1 if width <= 900 else 2)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("""
          () => renderDeviceAuthStatus(document.querySelector('.model-card-status'), {
            model_id: 'model-browser-check',
            login_url: 'https://auth.openai.com/codex/device',
            device_code: 'AB12-CD345',
            detail: '请完成授权',
          })
        """)
        copy_code = page.locator(".copy-device-code")
        expect(copy_code).to_be_visible()
        expect(copy_code).to_have_text("复制")
        copy_code.click()
        expect(copy_code).to_have_text("已复制")
        assert page.evaluate("window.__copiedDeviceCode") == "AB12-CD345"
        assert page.locator("#system-disclosure > summary").evaluate(
            "element => getComputedStyle(element).minHeight"
        ) == "68px"
        expect(page.locator("#resource-page .public-image-downloads")).to_be_attached()
        expect(page.locator("#resource-page .image-download-row")).to_have_count(2)
        expect(page.locator(".runtime-role-card")).to_have_count(4)
        expect(page.locator("#system-summary")).to_contain_text("角色环境")
        expect(page.locator("#nodes-disclosure summary").first).to_contain_text("TaskHub 节点")
        page.locator("#nodes-disclosure summary").first.click()
        expect(page.locator("#nodes.management-card-grid")).to_be_visible()
        expect(page.locator("#managed-containers.management-card-grid")).to_be_visible()
        expect(page.locator("#container-target")).to_have_count(0)
        expect(page.locator("#node-upgrade-disclosure")).to_have_count(0)
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
        expect(page.locator("#resource-page .public-image-downloads")).to_be_visible()
        expect(page.locator("#platform-settings .management-card")).to_have_count(4)
        expect(page.locator("#platform-settings-form .configuration-card")).to_have_count(5)
        expect(page.locator(".backup-contract")).to_be_attached()
        platform_form = page.locator("#platform-settings-form").locator("xpath=..")
        platform_form.evaluate("element => { element.open = true; }")
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
        page.locator("#system-disclosure").evaluate("element => { element.open = true; }")
        assert len(page.locator(".runtime-role-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns"
        ).split()) == 2

        for width, height in ((768, 900), (680, 900), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(100)
            assert page.evaluate("window.innerWidth") == width
            assert page.evaluate("window.matchMedia('(max-width: 680px)').matches") == (
                width <= 680
            )
            page.locator("#system-disclosure").evaluate("element => { element.open = true; }")
            page.locator("#platform-disclosure").evaluate("element => { element.open = true; }")
            assert len(page.locator(".runtime-role-grid").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ).split()) == 1
            address_columns = len(page.locator(".platform-address-fields").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ).split())
            assert address_columns == (1 if width == 390 else 2)
            page.locator("#nodes-disclosure").evaluate("element => { element.open = true; }")
            node_columns = len(
                page.locator("#container-form .node-configuration-grid").evaluate(
                    "element => getComputedStyle(element).gridTemplateColumns"
                ).split()
            )
            assert node_columns == 1
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            if width in {680, 768}:
                menu = page.locator(".side-menu").bounding_box()
                content = page.locator("#resource-page").bounding_box()
                assert menu["x"] + menu["width"] <= content["x"]
        unexpected_errors = [
            error for error in errors if error != "productized orchestration is disabled"
        ]
        assert not unexpected_errors
        browser.close()
