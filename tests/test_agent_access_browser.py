"""Real-browser regression for browser-approved development-agent access."""

import os
import socket

import httpx
import pytest
from cryptography.fernet import Fernet

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from tests.test_task_center_browser import serve

pytestmark = pytest.mark.skipif(
    os.getenv("TASKHUB_TEST_BROWSER") != "1", reason="Browser acceptance is opt-in"
)


def test_agent_pairing_approval_is_responsive_and_revocable(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    settings = Settings(
        checkpointer="memory",
        admin_token="agent-browser-password",
        session_secret="agent-browser-session",
        config_encryption_key=Fernet.generate_key().decode(),
        agent_access_file=str(tmp_path / "agent-access.json"),
        session_state_file=str(tmp_path / "sessions.json"),
        users_file=str(tmp_path / "users.json"),
        projects_file=str(tmp_path / "projects.json"),
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with (
        sync_playwright() as playwright,
        serve(create_app(settings), port),
        httpx.Client(base_url=url, trust_env=False) as client,
    ):
        pairing = client.post(
            "/api/auth/agent-pairings/start", json={"label": "Windows 试点机 · 长设备名称"}
        ).json()
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        page.locator("#admin-token").fill("agent-browser-password")
        page.locator("#login-button").click()
        page.locator("#onboarding-later").click()
        page.locator("#nav-resources").click()
        page.locator("#platform-disclosure > summary").click()
        page.locator("#access-security-disclosure > summary").click()

        row = page.locator("#agent-pairings [data-pairing-id]")
        expect(row).to_have_count(1)
        expect(row).to_contain_text(pairing["user_code"])
        for width in (1440, 768, 390):
            page.set_viewport_size({"width": width, "height": 900})
            expect(row).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            if width == 390:
                assert row.locator(".approve-agent").bounding_box()["height"] >= 44
                assert row.locator(".reject-agent").bounding_box()["height"] >= 44

        page.set_viewport_size({"width": 1440, "height": 1000})
        row.locator(".approve-agent").click()
        expect(page.locator("#agent-access-message")).to_have_text("")
        exchanged = client.post(
            f"/api/auth/agent-pairings/{pairing['pairing_id']}/exchange",
            json={"device_secret": pairing["device_secret"]},
        )
        assert exchanged.status_code == 200
        token = exchanged.json()["token"]
        bearer = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/projects", headers=bearer).status_code == 200

        page.locator("#refresh-agent-access").click()
        credential = page.locator("#agent-credentials .agent-credential-row")
        expect(credential).to_have_count(1)
        expect(credential).to_contain_text("项目负责人")
        page.on("dialog", lambda dialog: dialog.accept())
        credential.locator(".revoke-agent").click()
        expect(credential).to_contain_text("已撤销")
        assert client.get("/api/projects", headers=bearer).status_code == 401
        assert not errors
        browser.close()
