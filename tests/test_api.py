import json

from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings


def settings(**overrides):
    return Settings(
        checkpointer="memory",
        admin_token="admin-secret",
        session_secret="session-secret",
        **overrides,
    )


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"token": "admin-secret"})
    assert response.status_code == 200
    return {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf")}


def test_http_approval_flow():
    app = create_app(settings())
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/runs",
            json={"project_id": "demo", "requirement": "Build a status page"},
            headers=headers,
        )
        assert response.status_code == 201
        waiting = response.json()
        assert waiting["stage"] == "plan_approval"
        assert waiting["status"] == "waiting"

        response = client.post(
            f"/api/runs/{waiting['run_id']}/approval",
            json={"decision": "approve", "comment": "ok"},
            headers=headers,
        )
        assert response.status_code == 200
        publication_waiting = response.json()
        assert publication_waiting["stage"] == "merge_approval"

        response = client.post(
            f"/api/runs/{waiting['run_id']}/resume",
            json={"decision": "approve", "comment": "publish"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"


def test_unknown_run_returns_404():
    app = create_app(settings())
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/runs/missing").status_code == 404


def test_provider_status_masks_api_keys(tmp_path):
    app_settings = settings(
        codex_cli_bin="/missing/codex",
        codex_plus_home=str(tmp_path / "plus"),
        codex_pro_home=str(tmp_path / "pro"),
        gpt_api_key="abcdefghijkl",
    )
    with TestClient(create_app(app_settings)) as client:
        login(client)
        response = client.get("/api/providers")
        assert response.status_code == 200
        body = response.text
        assert "abcdefghijkl" not in body
        assert "abcd********ijkl" in body
        payload = response.json()
        providers = {item["id"]: item for item in payload["providers"]}
        assert providers["chatgpt_plus_account"]["role_models"] == {
            "planner": "account_default",
            "coder": "account_default",
            "supervisor": "account_default",
        }
        assert providers["gpt_api"]["role_models"] == {
            "planner": app_settings.gpt_planner_model,
            "coder": app_settings.gpt_coder_model,
            "supervisor": app_settings.gpt_supervisor_model,
        }


def test_mutating_api_requires_session_and_csrf():
    with TestClient(create_app(settings())) as client:
        payload = {"project_id": "demo", "requirement": "Protected request"}
        assert client.post("/api/runs", json=payload).status_code == 401
        login(client)
        assert client.post("/api/runs", json=payload).status_code == 403


def test_node_status_api_exposes_configured_nodes(tmp_path):
    nodes = tmp_path / "nodes.json"
    nodes.write_text(
        json.dumps({"nodes": [{"id": "controller-local", "kind": "local"}]}),
        encoding="utf-8",
    )
    app_settings = settings(
        test_runner="scheduled",
        nodes_file=str(nodes),
        node_state_file=str(tmp_path / "node-state.json"),
    )
    with TestClient(create_app(app_settings)) as client:
        login(client)
        response = client.get("/api/nodes")
    assert response.status_code == 200
    assert response.json()["nodes"][0]["node_id"] == "controller-local"


def test_system_config_api_exposes_safe_diagnostics(tmp_path):
    app_settings = settings(
        projects_file=str(tmp_path / "projects.json"),
        nodes_file=str(tmp_path / "nodes.json"),
        provider_secrets_file=str(tmp_path / "providers.env"),
        workspace_root=str(tmp_path / "workspaces"),
        artifact_root=str(tmp_path / "artifacts"),
    )
    (tmp_path / "projects.json").write_text('{"projects": []}', encoding="utf-8")
    (tmp_path / "nodes.json").write_text('{"nodes": []}', encoding="utf-8")
    (tmp_path / "providers.env").write_text("TASKHUB_ADMIN_TOKEN=hidden\n", encoding="utf-8")
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "artifacts").mkdir()

    with TestClient(create_app(app_settings)) as client:
        login(client)
        response = client.get("/api/system/config")

    assert response.status_code == 200
    body = response.text
    assert "admin-secret" not in body
    assert "hidden" not in body
    payload = response.json()
    assert payload["controller"]["role"] == "controller"
    assert {item["name"] for item in payload["controller"]["checks"]} >= {
        "操作系统",
        "Codex CLI",
        "User namespace",
        "Provider 配置",
    }
