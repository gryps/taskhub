"""Real-browser layout checks for Phase 1 product specifications."""

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


def test_product_spec_card_layout(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "产品化示例",
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
        admin_token="browser-product-token",
        session_secret="browser-product-session",
        projects_file=str(projects_file),
        production_orchestration_enabled=True,
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    executable = os.getenv("TASKHUB_TEST_BROWSER_EXECUTABLE") or None

    with sync_playwright() as playwright, serve(create_app(settings), port):
        browser = playwright.chromium.launch(executable_path=executable)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="networkidle")
        page.locator("#admin-token").fill("browser-product-token")
        page.locator("#login-button").click()
        expect(page.locator("#onboarding-page")).to_be_visible()
        page.locator("#onboarding-later").click()

        csrf = next(
            cookie["value"] for cookie in context.cookies() if cookie["name"] == "taskhub_v2_csrf"
        )
        created = context.request.post(
            f"{url}/api/requirements",
            headers={"X-CSRF-Token": csrf},
            data={
                "project_id": "demo",
                "original_text": "为管理员增加状态页，并展示最近一次检查结果。",
            },
        )
        assert created.status == 201
        contract = context.request.post(
            f"{url}/api/projects/demo/project-contracts/draft",
            headers={"X-CSRF-Token": csrf},
            data={"profile_id": "python-service", "inferred": False},
        )
        assert contract.status == 201
        contract_body = contract.json()
        contract_root = (
            f"{url}/api/projects/demo/project-contracts/{contract_body['contract_id']}"
            f"/versions/{contract_body['version']}"
        )
        assert context.request.post(f"{contract_root}/review", headers={"X-CSRF-Token": csrf}).ok
        assert context.request.post(f"{contract_root}/activate", headers={"X-CSRF-Token": csrf}).ok
        detail = context.request.get(f"{url}/api/product-specs/current?project_id=demo").json()
        spec = detail["product_spec"]
        if detail["decisions"]:
            decision = detail["decisions"][0]
            answers = {item["key"]: "按批准项目契约执行" for item in decision["questions"]}
            resolved = context.request.post(
                f"{url}/api/projects/demo/product-decisions/{decision['decision_id']}/resolve",
                headers={"X-CSRF-Token": csrf},
                data={"answers": answers},
            )
            assert resolved.ok
        spec_root = (
            f"{url}/api/product-specs/{spec['spec_id']}/versions/{spec['version']}?project_id=demo"
        )
        assert context.request.post(
            spec_root.replace("?", "/review?"), headers={"X-CSRF-Token": csrf}
        ).ok
        assert context.request.post(
            spec_root.replace("?", "/approve?"), headers={"X-CSRF-Token": csrf}
        ).ok
        started = context.request.post(
            f"{url}/api/runs",
            headers={"X-CSRF-Token": csrf},
            data={
                "project_id": "demo",
                "requirement": "ignored because the approved specification is authoritative",
                "product_spec_id": spec["spec_id"],
                "product_spec_version": spec["version"],
            },
        )
        assert started.status == 201
        page.locator("#nav-workflow").click()
        page.evaluate("Promise.all([loadCurrentProductSpec(), loadCurrentProjectContract()])")
        page.evaluate(
            "run => { currentRun = run.run_id; render(run); }",
            started.json(),
        )

        disclosure = page.locator("#product-spec-disclosure")
        expect(disclosure).to_be_visible()
        disclosure.locator("summary").click()
        expect(page.locator("#product-spec-state")).to_have_text("已批准")
        expect(page.locator("#product-spec-sections .product-spec-section")).to_have_count(11)
        expect(page.locator("#product-decision-form")).to_be_hidden()
        contract_disclosure = page.locator("#project-contract-disclosure")
        expect(contract_disclosure).to_be_visible()
        contract_disclosure.locator("summary").click()
        expect(page.locator("#project-contract-state")).to_have_text("已生效")
        expect(page.locator("#project-contract-facts > div")).to_have_count(6)
        policy_disclosure = page.locator("#scheduling-policy-disclosure")
        expect(policy_disclosure).to_be_visible()
        policy_disclosure.locator("summary").click()
        expect(page.locator("#scheduling-policy-summary")).to_contain_text("并发 2")
        execution_disclosure = page.locator("#execution-plan-disclosure")
        expect(execution_disclosure).to_be_visible()
        execution_disclosure.locator("summary").click()
        expect(page.locator("#execution-plan-state")).to_have_text("已完成")
        expect(page.locator("#execution-plan-facts > div")).to_have_count(4)
        expect(page.locator("#execution-analysis .execution-analysis-facts > div")).to_have_count(6)
        assert page.locator("#execution-tasks .execution-task-card").count() >= 2
        assert (
            page.locator("#product-spec-title").evaluate(
                "element => getComputedStyle(element).fontSize"
            )
            == "14px"
        )
        assert (
            page.locator(".product-spec-section li").first.evaluate(
                "element => getComputedStyle(element).fontSize"
            )
            == "13px"
        )

        for width, height, columns in ((1440, 1000, 2), (680, 900, 1), (390, 844, 1)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(100)
            actual_columns = len(
                page.locator("#product-spec-sections")
                .evaluate("element => getComputedStyle(element).gridTemplateColumns")
                .split()
            )
            assert actual_columns == columns
            contract_columns = len(
                page.locator("#project-contract-facts")
                .evaluate("element => getComputedStyle(element).gridTemplateColumns")
                .split()
            )
            assert contract_columns == columns
            dag_columns = len(
                page.locator("#execution-tasks")
                .evaluate("element => getComputedStyle(element).gridTemplateColumns")
                .split()
            )
            assert dag_columns == columns
            policy_columns = len(
                page.locator(".scheduling-policy-fields")
                .evaluate("element => getComputedStyle(element).gridTemplateColumns")
                .split()
            )
            assert policy_columns == (3 if width == 1440 else 1)
            analysis_columns = len(
                page.locator(".execution-analysis-facts")
                .evaluate("element => getComputedStyle(element).gridTemplateColumns")
                .split()
            )
            assert analysis_columns == (3 if width == 1440 else 1)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )

        assert not errors
        context.close()
        browser.close()
