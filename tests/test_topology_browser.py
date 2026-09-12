"""Real-browser acceptance for the independent production topology canvas."""

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
def test_topology_canvas_has_equivalent_controls_and_no_overflow(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    projects = tmp_path / "projects.json"
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "画布示例",
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
        admin_token="topology-browser-token",
        session_secret="topology-browser-session",
        projects_file=str(projects),
        operations_log_file=str(tmp_path / "operations.jsonl"),
        production_orchestration_enabled=True,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with sync_playwright() as playwright, serve(create_app(settings), port):
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        page.locator("#admin-token").fill("topology-browser-token")
        page.locator("#login-button").click()
        canvas_response = page.goto(f"{url}/canvas/", wait_until="networkidle")
        assert canvas_response
        assert canvas_response.status == 200
        assert "no-store" in canvas_response.headers["cache-control"]
        expect(page.get_by_role("heading", name="项目生产画布")).to_be_visible()
        pane = page.locator(".react-flow__pane")
        pane.click(button="right", position={"x": 125, "y": 410})
        page.get_by_role("menuitem", name="添加执行节点").click()
        expect(page.locator(".topology-node")).to_have_count(3)
        expect(page.locator(".status.draft")).to_be_visible()
        expect(page.get_by_text("已创建草稿并添加节点，请配置后保存")).to_be_visible()
        added_box = page.locator(".topology-node").last.bounding_box()
        pane_box = pane.bounding_box()
        assert added_box and pane_box
        assert abs((added_box["x"] + added_box["width"] / 2) - (pane_box["x"] + 125)) < 30
        assert abs((added_box["y"] + added_box["height"] / 2) - (pane_box["y"] + 410)) < 30
        page.get_by_role("button", name="＋执行节点").click()
        page.get_by_role("button", name="列表").click()
        expect(page.locator(".list-card")).to_have_count(4)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()
