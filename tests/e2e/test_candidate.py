"""Real headed browser checks; missing infrastructure is a failure, never a skip."""
import json
import os
from html import escape
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
    result["scenarios"] = []

    def completed(scenario):
        record_property(f"scenario.{scenario}", "passed")
        result["scenarios"].append({"id": scenario, "status": "passed"})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False, channel="msedge" if browser_name == "edge" else "chrome"
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
                expect(page.locator("#status")).not_to_have_text("已阻塞")
                expect(page.locator("#approve")).to_have_text("批准计划")
                expect(page.locator("#reject")).to_have_text("拒绝")
                expect(page.locator("#revise")).to_be_hidden()
                expect(page.locator("#archive-task")).to_be_hidden()
                page.reload()
                page.locator(f'tr[data-run-id="{run_id}"]').click()
                expect(page.locator("#status")).to_have_text("待处理")
            completed("login_status")
            completed("refresh_consistency")
            states = [context.request.get(f"{url}/api/runs/{run_id}").json()
                      for context in contexts]
            assert states[0] == states[1]

            completed("dual_context_consistency")

            # Exercise a recoverable failure through the real UI. The disposable
            # publisher fails once per run and then succeeds, giving both clients
            # an observable blocked -> recovered transition.
            pages[0].locator("#approve").click()
            expect(pages[0].locator("#approve")).to_have_text("合并到权威分支")
            pages[0].locator("#acceptance-detail").evaluate("element => element.parentElement.open = true")
            with pages[0].expect_download() as artifact_download_info:
                pages[0].locator("#acceptance-detail a").click()
            artifact_download = artifact_download_info.value
            assert "Candidate artifact available" in Path(artifact_download.path()).read_text()
            completed("artifact_view")
            pages[0].locator("#approve").click()
            expect(pages[0].locator("#status")).to_have_text("已阻塞")
            blocked = contexts[0].request.get(f"{url}/api/runs/{run_id}").json()
            reason = blocked["blocking_reason"]
            assert reason["code"] == "TransientPublicationError"
            assert reason["responsible_node"] == "publisher"
            assert reason["model"] == "none"
            assert "retry" in reason["recommended_action"]
            expect(pages[0].locator("#blocking-code")).to_have_text("TransientPublicationError")
            expect(pages[0].locator("#blocking-node")).to_have_text("publisher")
            expect(pages[0].locator("#blocking-model")).to_have_text("none")
            expect(pages[0].locator("#blocking-action")).to_have_text(reason["recommended_action"])
            completed("structured_blocking")
            expect(pages[0].locator("#approve")).to_have_text("重新检查并发布")
            expect(pages[0].locator("#reject")).to_have_text("取消任务")
            expect(pages[0].locator("#retry-countdown")).to_contain_text("自动重试")
            initial_countdown = pages[0].locator("#retry-countdown").inner_text()
            expect(pages[0].locator("#retry-countdown")).not_to_have_text(initial_countdown)
            pages[0].screenshot(path=str(output / "screenshots" /
                f"{browser_name}-blocked.png"), full_page=True)

            expect(pages[0].locator("#status")).to_have_text("已完成", timeout=10_000)
            pages[1].reload()
            pages[1].locator(f'tr[data-run-id="{run_id}"]').click()
            expect(pages[1].locator("#status")).to_have_text("已完成")
            expect(pages[1].locator("#approve")).to_be_hidden()
            recovered = [context.request.get(f"{url}/api/runs/{run_id}").json()
                         for context in contexts]
            assert recovered[0] == recovered[1]
            completed("retry_recovery")
            expect(pages[0].locator("#archive-task")).to_be_visible()
            expect(pages[0].locator("#acceptance-submit")).to_be_hidden()

            owner_response = contexts[0].request.post(
                f"{url}/api/runs", headers={"X-CSRF-Token": csrf},
                data={"project_id": f"owner-{uuid4().hex}",
                      "requirement": "[acceptance:revision-limit] Verify owner actions"},
            )
            assert owner_response.status == 201, owner_response.text()
            owner_run_id = owner_response.json()["run_id"]
            pages[0].locator("#nav-tasks").click()
            pages[0].locator("#refresh-tasks").click()
            pages[0].locator(f'tr[data-run-id="{owner_run_id}"]').click()
            pages[0].locator("#approve").click()
            expect(pages[0].locator("#action-title")).to_have_text("返工次数已达上限")
            expect(pages[0].locator("#configure-resources")).to_be_visible()
            expect(pages[0].locator("#add-evidence")).to_be_visible()
            expect(pages[0].locator("#manual")).to_be_visible()
            expect(pages[0].locator("#reject")).to_have_text("终止任务")
            pages[0].locator("#add-evidence").click()
            expect(pages[0].locator("#acceptance-submit")).to_be_visible()
            pages[0].locator("#configure-resources").click()
            expect(pages[0].locator("#resource-page")).to_be_visible()
            expect(pages[0].locator("#nodes")).to_contain_text("windows-gui-34")
            expect(pages[0].locator("#nodes")).to_contain_text("windows_gui")
            expect(pages[0].locator("#nodes")).to_contain_text("chromium")
            expect(pages[0].locator("#nodes")).to_contain_text("edge")
            completed("resource_center")
            completed("action_visibility")
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
            (report / f"{browser_name}.html").write_text(
                "<!doctype html><meta charset=utf-8><title>Playwright acceptance</title>"
                f"<h1>{escape(browser_name)}: {escape(result['status'])}</h1>"
                f"<p>Target: {escape(url)}</p><p>Commit: <code>{escape(commit)}</code></p>"
                "<ul>" + "".join(f"<li>{escape(item['id'])}: {escape(item['status'])}</li>"
                                  for item in result["scenarios"]) + "</ul>"
                f"<p>Browser version: {escape(str(result.get('version', 'unknown')))}</p>",
                encoding="utf-8",
            )
