"""Real headed browser checks; missing infrastructure is a failure, never a skip."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.parametrize("browser_name", ["chromium", "edge"])
def test_candidate_approval_consistency(browser_name, record_property):
    from playwright.sync_api import expect, sync_playwright

    assert os.name == "nt", "Windows GUI node is required"
    url = os.environ["TASKHUB_TARGET_URL"].rstrip("/")
    commit = os.environ["TASKHUB_GIT_COMMIT"]
    assert url and len(commit) == 40
    record_property("browser", browser_name)
    record_property("target_url", url)
    record_property("git_commit", commit)
    output = Path("test-results")
    for directory in ("screenshots", "traces", "videos"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    report = Path("playwright-report")
    report.mkdir(exist_ok=True)
    contexts = []
    result = {"browser": browser_name, "target_url": url, "git_commit": commit,
              "status": "failed"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False, channel="msedge" if browser_name == "edge" else None
        )
        result["version"] = browser.version
        record_property("browser_version", browser.version)
        try:
            pages = []
            for index in range(2):
                context = browser.new_context(record_video_dir=str(output / "videos"))
                contexts.append(context)
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                page = context.new_page()
                pages.append(page)
                page.goto(url)
                page.locator("#admin-token").fill("acceptance-only")
                page.locator("#login-button").click()
                expect(page.locator("#task-center")).to_be_visible()
            identity = contexts[0].request.get(f"{url}/api/health")
            assert identity.ok and identity.json()["git_commit"] == commit
            csrf = next(c["value"] for c in contexts[0].cookies()
                        if c["name"] == "taskhub_v2_csrf")
            response = contexts[0].request.post(
                f"{url}/api/runs", headers={"X-CSRF-Token": csrf},
                data={"project_id": f"browser-{uuid4().hex}",
                      "requirement": "Verify candidate approval consistency"},
            )
            assert response.status == 201, response.text()
            run_id = response.json()["run_id"]
            for page in pages:
                page.locator("#refresh-tasks").click()
                page.locator(f'tr[data-run-id="{run_id}"]').click()
                expect(page.locator("#status")).to_have_text("待处理")
                expect(page.locator("#approve")).to_have_text("批准计划")
                page.reload()
                page.locator(f'tr[data-run-id="{run_id}"]').click()
                expect(page.locator("#status")).to_have_text("待处理")
            states = [context.request.get(f"{url}/api/runs/{run_id}").json()
                      for context in contexts]
            assert states[0] == states[1]
            result["status"] = "passed"
        finally:
            for index, context in enumerate(contexts):
                try:
                    for page in context.pages:
                        page.screenshot(path=str(output / "screenshots" /
                            f"{browser_name}-{index}-{result['status']}.png"), full_page=True)
                    context.tracing.stop(path=str(output / "traces" /
                        f"{browser_name}-{index}-trace.zip"))
                finally:
                    context.close()
            browser.close()
            (report / f"{browser_name}.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
