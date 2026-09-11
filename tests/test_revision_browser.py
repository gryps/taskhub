"""Real-browser layout checks for Phase 4 change requests."""

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
def test_revision_surface_uses_shared_cards_and_does_not_overflow(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "持续修订示例",
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
        admin_token="revision-browser-token",
        session_secret="revision-browser-session",
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
        page = browser.new_page(viewport={"width": width, "height": 950})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
        page.locator("#admin-token").fill("revision-browser-token")
        page.locator("#login-button").click()
        page.locator("#onboarding-later").click()
        page.locator("#nav-workflow").click()
        disclosure = page.locator("#change-request-disclosure")
        expect(disclosure).to_be_visible()
        disclosure.locator("summary").click()
        expect(page.locator("#change-request-form")).to_be_visible()
        assert (
            page.locator("#change-request-form h3").evaluate(
                "element => getComputedStyle(element).fontSize"
            )
            == "14px"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()
