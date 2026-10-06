"""Real-browser checks for native Windows node onboarding."""

import os
import socket

import pytest

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from tests.test_task_center_browser import serve

pytestmark = pytest.mark.skipif(
    os.getenv("TASKHUB_TEST_BROWSER") != "1", reason="Browser acceptance is opt-in"
)


def test_external_windows_node_onboarding_is_actionable_and_responsive(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    settings = Settings(
        checkpointer="memory",
        admin_token="windows-browser-token",
        session_secret="windows-browser-session",
        projects_file=str(tmp_path / "projects.json"),
        nodes_file=str(tmp_path / "nodes.json"),
        node_credentials_file=str(tmp_path / "node-credentials.json"),
        external_windows_nodes_file=str(tmp_path / "external-windows.json"),
        provider_secrets_file=str(tmp_path / "providers.env"),
        provider_health_file=str(tmp_path / "provider-health.json"),
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    with sync_playwright() as playwright, serve(create_app(settings), port):
        browser = playwright.chromium.launch(
            executable_path=os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        page.locator("#admin-token").fill("windows-browser-token")
        page.locator("#login-button").click()
        if page.locator("#onboarding-page").is_visible():
            page.locator("#onboarding-later").click()

        page.route(
            "**/api/external-windows-nodes",
            lambda route: route.fulfill(json={"nodes": []})
            if route.request.method == "GET"
            else route.continue_(),
        )
        page.route(
            "**/api/external-windows-nodes/probe",
            lambda route: route.fulfill(
                json={
                    "status": "confirmation_required",
                    "detail": "请核对主机指纹",
                    "fingerprint": "SHA256:abcdefghijklmnopqrstuvwxyz123456",
                    "facts": {},
                }
            ),
        )
        page.locator("#nav-resources").click()
        page.locator("#nodes-disclosure").evaluate("element => { element.open = true; }")
        page.locator("#external-windows-disclosure > summary").click()
        expect(page.locator("#external-windows-form")).to_be_visible()
        expect(page.locator("#external-windows-nodes")).to_contain_text("尚未接入")
        page.locator("#external-windows-node-id").fill("windows-test-34")
        page.locator("#external-windows-display-name").fill("Windows 实测机")
        page.locator("#external-windows-address").fill("192.168.31.34")
        page.locator("#external-windows-username").fill("user")
        page.locator("#external-windows-private-key").fill(
            "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"
        )
        page.locator("#probe-external-windows").click()
        expect(page.locator("#external-windows-fingerprint")).to_have_value(
            "SHA256:abcdefghijklmnopqrstuvwxyz123456"
        )
        expect(page.locator("#install-external-windows")).to_be_disabled()
        page.locator("#external-windows-confirm-fingerprint").check()
        expect(page.locator("#install-external-windows")).to_be_enabled()

        for width in (1440, 768, 390):
            page.set_viewport_size({"width": width, "height": 900})
            expect(page.locator("#external-windows-form")).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
        assert errors == []
        browser.close()
