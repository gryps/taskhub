"""Real-browser acceptance for Phase 5 capability-pack surfaces."""

import json
import os
import socket

import pytest

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from tests.test_task_center_browser import serve

pytestmark = pytest.mark.skipif(
    os.getenv("TASKHUB_TEST_BROWSER") != "1", reason="Browser acceptance is opt-in"
)


@pytest.mark.parametrize("width", [1440, 680, 390])
def test_capability_cards_lock_design_and_do_not_overflow(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "能力包示例",
                        "repository": str(tmp_path),
                        "base_ref": "main",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        checkpointer="memory",
        provider="deterministic",
        admin_token="capability-browser-token",
        session_secret="capability-browser-session",
        projects_file=str(projects_file),
        production_orchestration_enabled=True,
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with sync_playwright() as playwright, serve(create_app(settings), port):
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
        page.locator("#admin-token").fill("capability-browser-token")
        page.locator("#login-button").click()
        page.locator("#onboarding-later").click()
        page.locator("#nav-workflow").click()
        page.locator("#project-contract-disclosure > summary").click()
        page.locator("#project-contract-profile").select_option("frontend-spa")
        page.locator("#create-project-contract").click()
        expect(page.locator("#review-project-contract")).to_be_visible()
        page.locator("#review-project-contract").click()
        expect(page.locator("#activate-project-contract")).to_be_visible()
        page.locator("#activate-project-contract").click()
        expect(page.locator("#project-contract-state")).to_have_text("已生效")
        page.evaluate(
            """async () => {
              const csrf = document.cookie.split('; ').find((item) =>
                item.startsWith('taskhub_v2_csrf='))?.split('=').slice(1).join('=');
              const response = await fetch('/api/requirements', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'X-CSRF-Token': decodeURIComponent(csrf)
                },
                body: JSON.stringify({
                  project_id: 'demo',
                  original_text: '为管理员增加响应式状态页。\\n' +
                    '验收标准：桌面和手机显示状态。\\n部署到预生产环境。'
                })
              });
              if (!response.ok) throw new Error(await response.text());
            }"""
        )
        page.reload(wait_until="networkidle")
        page.locator("#nav-workflow").click()
        disclosure = page.locator("#capability-disclosure")
        expect(disclosure).to_be_visible()
        disclosure.locator("> summary").click()
        expect(page.locator(".capability-option-card")).to_have_count(3)
        assert (
            page.locator(".capability-option-card h3").first.evaluate(
                "element => getComputedStyle(element).fontSize"
            )
            == "14px"
        )
        page.locator("[data-select-packs]").first.click()
        expect(page.locator("[data-activate-lock]")).to_be_visible()
        page.locator("[data-activate-lock]").click()
        expect(page.locator("#active-design-state")).to_have_text("已锁定")
        page.locator("#nav-resources").click()
        page.locator("#platform-disclosure > summary").click()
        page.locator("#capability-inventory-disclosure > summary").click()
        expect(page.locator(".capability-inventory-card")).to_have_count(7)
        first_card_box = page.locator(".capability-inventory-card").first.bounding_box()
        first_fact_label_box = page.locator(".capability-inventory-card dt").first.bounding_box()
        first_summary = page.locator(".capability-inventory-card > p").first
        disclosure_box = page.locator("#capability-inventory-disclosure").bounding_box()
        expected_inset = 10 if width == 390 else 15
        assert first_card_box["x"] >= disclosure_box["x"] + expected_inset
        assert first_fact_label_box["x"] >= first_card_box["x"] + 14
        assert first_summary.evaluate("element => getComputedStyle(element).paddingLeft") == "15px"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()
