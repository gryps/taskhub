#!/usr/bin/env python3
"""Drive the disposable acceptance service with a real Microsoft Edge browser."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def run_acceptance(url, output):
    output.mkdir(parents=True, exist_ok=True)
    project = f"edge-{uuid4().hex}"
    errors = []

    def login(browser):
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.locator("#admin-token").fill("acceptance-only")
        page.locator("#login-button").click()
        expect(page.locator("#task-center")).to_be_visible()
        page.locator("#filter-project").fill(project)
        return context, page

    def rows(page, count):
        expect(page.locator("#task-rows tr")).to_have_count(count)

    def action(page, stage, label):
        panel = page.locator(f'#action[data-stage="{stage}"]')
        expect(panel).to_be_visible()
        button = panel.locator("#approve")
        expect(button).to_have_text(label)
        button.click()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        context, page = login(browser)
        csrf = next(c["value"] for c in context.cookies() if c["name"] == "taskhub_v2_csrf")
        ids = []
        for number, line in enumerate(("A", "B", "A")):
            response = context.request.post(
                f"{url}/api/runs",
                headers={"X-CSRF-Token": csrf},
                data={
                    "project_id": project,
                    "requirement": f"Edge acceptance feature {number}",
                    "production_line": line,
                },
            )
            assert response.status == 201, response.text()
            ids.append(response.json()["run_id"])
        page.locator("#refresh-tasks").click()
        rows(page, 3)
        page.screenshot(path=str(output / "task-list.png"), full_page=True)
        page.locator("#nav-resources").click()
        expect(page.locator("#resource-page")).to_be_visible()
        expect(page.locator("#providers .resource-row:not(.resource-header)")).not_to_have_count(
            0, timeout=30_000
        )
        expect(page.locator("#nodes .resource-row:not(.resource-header)")).not_to_have_count(
            0, timeout=30_000
        )
        expect(page.locator("#resource-page")).not_to_contain_text("sk-")
        page.screenshot(path=str(output / "system-resources.png"), full_page=True)
        page.locator("#nav-tasks").click()
        page.reload()
        page.locator("#filter-project").fill(project)
        rows(page, 3)
        page.locator("#filter-line").fill("B")
        rows(page, 1)
        expect(page.locator("#task-rows")).to_contain_text(ids[1])
        page.locator("#filter-line").fill("A")
        page.locator("#filter-status").select_option("waiting")
        page.locator("#filter-stage").select_option("plan_approval")
        rows(page, 2)
        page.locator(f'tr[data-run-id="{ids[0]}"]').click()
        expect(page.locator("#flow .step")).to_have_count(11)
        action(page, "plan_approval", "批准计划")
        action(page, "merge_approval", "合并到权威分支")
        expect(page.locator("#status")).to_have_text("已阻塞")
        expect(page.locator("#action-detail")).to_have_text("temporary publication failure")
        page.screenshot(path=str(output / "task-blocked.png"), full_page=True)
        timeline = context.request.get(f"{url}/api/runs/{ids[0]}").json()["timeline"]
        context.close()
        browser.close()

        # A new Edge process has no prior cookies or browser memory.
        browser = playwright.chromium.launch(channel="msedge")
        context, page = login(browser)
        rows(page, 3)
        page.set_viewport_size({"width": 680, "height": 900})
        expect(page.locator("#nav-tasks")).to_be_visible()
        page.set_viewport_size({"width": 1440, "height": 1000})
        expect(page.locator(f'tr[data-run-id="{ids[0]}"]')).to_contain_text(
            "temporary publication failure"
        )
        page.locator(f'tr[data-run-id="{ids[0]}"]').click()
        action(page, "merging", "重新检查并发布")
        expect(page.locator("#status")).to_have_text("已完成")
        expect(page.locator("#flow .step.done")).to_have_count(11)
        completed = context.request.get(f"{url}/api/runs/{ids[0]}").json()
        assert completed["timeline"][:len(timeline)] == timeline
        page.screenshot(path=str(output / "task-recovered.png"), full_page=True)
        context.close()
        browser.close()

    assert not errors, errors
    result = {
        "status": "passed",
        "browser": "Microsoft Edge",
        "screenshots": 4,
        "run_ids": ids,
    }
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


def main():
    args = parse_args()
    run_acceptance(args.url.rstrip("/"), Path(args.output))


if __name__ == "__main__":
    main()
