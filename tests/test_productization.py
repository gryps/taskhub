import json

from fastapi.testclient import TestClient

from taskhub_v2.api.app import _required_permission, create_app
from taskhub_v2.config import Settings


def productization_settings(tmp_path):
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "Demo",
                        "repository": str(tmp_path),
                        "base_ref": "main",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        checkpointer="memory",
        provider="deterministic",
        admin_token="admin-secret",
        session_secret="session-secret",
        projects_file=str(projects_file),
        production_orchestration_enabled=True,
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )


def login(client):
    response = client.post("/api/auth/login", json={"token": "admin-secret"})
    assert response.status_code == 200
    return {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf")}


def test_productization_permissions_are_project_scoped():
    assert _required_permission("/api/requirements", "GET") == "read"
    assert _required_permission("/api/requirements", "POST") == "projects:manage"
    assert _required_permission("/api/product-specs/spec/approve", "POST") == "projects:manage"


def create_clear_spec(client, headers):
    response = client.post(
        "/api/requirements",
        headers=headers,
        json={
            "project_id": "demo",
            "original_text": (
                "为管理员用户增加状态页。\n验收标准：页面显示服务状态。\n部署到现有预生产环境。"
            ),
            "attachments": [
                {
                    "name": "context.md",
                    "content_type": "text/markdown",
                    "size_bytes": 12,
                    "sha256": "a" * 64,
                    "summary": "业务背景",
                }
            ],
        },
    )
    assert response.status_code == 201
    return response.json()


def activate_contract(client, headers):
    draft = client.post(
        "/api/projects/demo/project-contracts/draft",
        headers=headers,
        json={"profile_id": "python-service", "inferred": False},
    ).json()
    root = (
        f"/api/projects/demo/project-contracts/{draft['contract_id']}/versions/{draft['version']}"
    )
    assert client.post(f"{root}/review", headers=headers).status_code == 200
    assert client.post(f"{root}/activate", headers=headers).status_code == 200
    return draft


def test_product_spec_must_be_approved_before_run(tmp_path):
    with TestClient(create_app(productization_settings(tmp_path))) as client:
        headers = login(client)
        contract = activate_contract(client, headers)
        blocked = client.post(
            "/api/runs",
            headers=headers,
            json={"project_id": "demo", "requirement": "bypass"},
        )
        assert blocked.status_code == 409
        assert "产品规格" in blocked.json()["detail"]

        created = create_clear_spec(client, headers)
        spec = created["product_spec"]
        assert created["decision"] is None
        assert spec["status"] == "draft"
        assert len(spec["content_digest"]) == 64

        still_blocked = client.post(
            "/api/runs",
            headers=headers,
            json={
                "project_id": "demo",
                "requirement": "bypass",
                "product_spec_id": spec["spec_id"],
                "product_spec_version": 1,
            },
        )
        assert still_blocked.status_code == 409

        root = f"/api/product-specs/{spec['spec_id']}/versions/1"
        reviewed = client.post(f"{root}/review?project_id=demo", headers=headers)
        assert reviewed.json()["status"] == "in_review"
        approved = client.post(f"{root}/approve?project_id=demo", headers=headers)
        assert approved.json()["status"] == "approved"

        immutable = client.put(
            f"{root}?project_id=demo",
            headers=headers,
            json={"summary": "silent rewrite"},
        )
        assert immutable.status_code == 409

        started = client.post(
            "/api/runs",
            headers=headers,
            json={
                "project_id": "demo",
                "requirement": "this text must not replace the approved source",
                "product_spec_id": spec["spec_id"],
                "product_spec_version": 1,
            },
        )
        assert started.status_code == 201
        run = started.json()
        assert run["product_spec_id"] == spec["spec_id"]
        assert run["product_spec_version"] == 1
        assert run["project_contract_id"] == contract["contract_id"]
        assert run["project_contract_version"] == 1
        assert run["requirement"].startswith("为管理员用户增加状态页")
        assert run["execution_plan"]["status"] == "completed"
        assert len(run["production_tasks"]) == 3

        assert len(run["execution_batches"]) == 2
        dag = client.get(f"/api/runs/{run['run_id']}/execution-plan").json()
        assert dag["task_page"] == {"page": 1, "page_size": 100, "total": 3}
        assert dag["latest_snapshot"]["status"] == "completed"


def test_approved_product_spec_still_requires_active_project_contract(tmp_path):
    with TestClient(create_app(productization_settings(tmp_path))) as client:
        headers = login(client)
        spec = create_clear_spec(client, headers)["product_spec"]
        root = f"/api/product-specs/{spec['spec_id']}/versions/1"
        client.post(f"{root}/review?project_id=demo", headers=headers)
        client.post(f"{root}/approve?project_id=demo", headers=headers)

        blocked = client.post(
            "/api/runs",
            headers=headers,
            json={
                "project_id": "demo",
                "requirement": "bypass",
                "product_spec_id": spec["spec_id"],
                "product_spec_version": 1,
            },
        )
        assert blocked.status_code == 409
        assert "项目合同" in blocked.json()["detail"]


def test_one_product_decision_resolves_all_missing_information(tmp_path):
    with TestClient(create_app(productization_settings(tmp_path))) as client:
        headers = login(client)
        activate_contract(client, headers)
        created = client.post(
            "/api/requirements",
            headers=headers,
            json={"project_id": "demo", "original_text": "增加导出功能"},
        ).json()
        spec = created["product_spec"]
        decision = created["decision"]
        assert decision["status"] == "pending"
        assert {item["key"] for item in decision["questions"]} == {
            "target_users",
            "acceptance_definition",
            "delivery_target",
        }
        review = client.post(
            f"/api/product-specs/{spec['spec_id']}/versions/1/review?project_id=demo",
            headers=headers,
        )
        assert review.status_code == 409

        resolved = client.post(
            f"/api/projects/demo/product-decisions/{decision['decision_id']}/resolve",
            headers=headers,
            json={
                "answers": {
                    "target_users": "项目负责人",
                    "acceptance_definition": "能够导出 CSV 并通过自动测试",
                    "delivery_target": "形成容器制品并部署预生产",
                }
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["pending_decision_ids"] == []
        detail = client.get(
            f"/api/product-specs/{spec['spec_id']}/versions/1?project_id=demo"
        ).json()
        assert detail["requirements"][0]["status"] == "productized"
        assert detail["decisions"][0]["status"] == "resolved"


def test_revision_creates_new_version_and_field_diff(tmp_path):
    with TestClient(create_app(productization_settings(tmp_path))) as client:
        headers = login(client)
        spec = create_clear_spec(client, headers)["product_spec"]
        root = f"/api/product-specs/{spec['spec_id']}/versions/1"
        client.post(f"{root}/review?project_id=demo", headers=headers)
        client.post(f"{root}/approve?project_id=demo", headers=headers)
        revision = client.post(
            f"{root}/revisions?project_id=demo",
            headers=headers,
            json={"reason": "增加审计要求"},
        )
        assert revision.status_code == 201
        assert revision.json()["version"] == 2
        assert revision.json()["previous_version"] == 1
        assert revision.json()["change_request_id"].startswith("cr_")

        updated = client.put(
            f"/api/product-specs/{spec['spec_id']}/versions/2?project_id=demo",
            headers=headers,
            json={"security_requirements": ["记录导出操作审计"]},
        )
        assert updated.status_code == 200
        diff = client.get(
            f"/api/product-specs/{spec['spec_id']}/diff",
            params={"project_id": "demo", "from_version": 1, "to_version": 2},
        )
        assert diff.status_code == 200
        assert "security_requirements" in {item["field"] for item in diff.json()["changes"]}

        client.post(
            f"/api/product-specs/{spec['spec_id']}/versions/2/review?project_id=demo",
            headers=headers,
        )
        approved = client.post(
            f"/api/product-specs/{spec['spec_id']}/versions/2/approve?project_id=demo",
            headers=headers,
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        previous = client.get(f"{root}?project_id=demo")
        assert previous.json()["product_spec"]["status"] == "superseded"


def test_requirement_original_is_preserved_and_supplements_append(tmp_path):
    with TestClient(create_app(productization_settings(tmp_path))) as client:
        headers = login(client)
        created = create_clear_spec(client, headers)
        requirement = created["requirement"]
        response = client.post(
            f"/api/requirements/{requirement['requirement_id']}/supplements",
            headers=headers,
            json={"project_id": "demo", "text": "补充：保留最近 30 天记录"},
        )
        assert response.status_code == 200
        stored = response.json()
        assert stored["original_text"] == requirement["original_text"]
        assert [item["text"] for item in stored["supplements"]] == ["补充：保留最近 30 天记录"]


def test_approved_spec_keeps_source_snapshot_until_revision(tmp_path):
    with TestClient(create_app(productization_settings(tmp_path))) as client:
        headers = login(client)
        activate_contract(client, headers)
        created = create_clear_spec(client, headers)
        spec = created["product_spec"]
        root = f"/api/product-specs/{spec['spec_id']}/versions/1"
        client.post(f"{root}/review?project_id=demo", headers=headers)
        client.post(f"{root}/approve?project_id=demo", headers=headers)
        client.post(
            f"/api/requirements/{created['requirement']['requirement_id']}/supplements",
            headers=headers,
            json={"project_id": "demo", "text": "补充：保留最近 30 天记录"},
        )

        original_run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "project_id": "demo",
                "requirement": "ignored",
                "product_spec_id": spec["spec_id"],
                "product_spec_version": 1,
            },
        ).json()
        assert "最近 30 天" not in original_run["requirement"]

        revision = client.post(
            f"{root}/revisions?project_id=demo",
            headers=headers,
            json={"reason": "纳入数据保留要求"},
        ).json()
        assert (
            "最近 30 天"
            in revision["source_requirement_snapshots"][created["requirement"]["requirement_id"]]
        )
