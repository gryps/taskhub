import importlib.util
import json
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings

SCRIPT_PATH = Path(__file__).parents[1] / "deploy" / "release" / "connect-agent.py"
CHECK_SCRIPT_PATH = (
    Path(__file__).parents[1] / "deploy" / "release" / "check-agent-connection.py"
)


def agent_settings(tmp_path):
    return Settings(
        checkpointer="memory",
        admin_token="admin-secret",
        session_secret="session-secret",
        config_encryption_key=Fernet.generate_key().decode(),
        agent_access_file=str(tmp_path / "agent-access.json"),
        users_file=str(tmp_path / "users.json"),
        session_state_file=str(tmp_path / "sessions.json"),
        operations_log_file=str(tmp_path / "operations.jsonl"),
        projects_file=str(tmp_path / "projects.json"),
    )


def login(client):
    response = client.post("/api/auth/login", json={"token": "admin-secret"})
    return {"X-CSRF-Token": response.cookies["taskhub_v2_csrf"]}


def pair_agent(client, headers, label="Codex on macOS"):
    started = client.post("/api/auth/agent-pairings/start", json={"label": label})
    assert started.status_code == 200
    pairing = started.json()
    approved = client.post(
        f"/api/auth/agent-pairings/{pairing['pairing_id']}/approve",
        headers=headers,
        json={"role": "project_owner", "expires_days": 30},
    )
    assert approved.status_code == 200
    exchanged = client.post(
        f"/api/auth/agent-pairings/{pairing['pairing_id']}/exchange",
        json={"device_secret": pairing["device_secret"]},
    )
    assert exchanged.status_code == 200
    return pairing, exchanged.json()["token"], approved.json()["credential_id"]


def test_agent_pairing_issues_one_time_revocable_bearer(tmp_path):
    settings = agent_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        pairing, token, credential_id = pair_agent(client, headers)

        repeated = client.post(
            f"/api/auth/agent-pairings/{pairing['pairing_id']}/exchange",
            json={"device_secret": pairing["device_secret"]},
        )
        assert repeated.status_code == 401

        bearer = {"Authorization": f"Bearer {token}"}
        status = client.get("/api/auth/status", headers=bearer)
        assert status.status_code == 200
        assert status.json()["authenticated"] is True
        assert status.json()["auth_type"] == "bearer"
        assert status.json()["role"] == "project_owner"
        assert client.get("/api/projects", headers=bearer).status_code == 200

        revoked = client.delete(
            f"/api/auth/agent-credentials/{credential_id}", headers=headers
        )
        assert revoked.status_code == 200
        assert client.get("/api/projects", headers=bearer).status_code == 401

    stored = (tmp_path / "agent-access.json").read_text(encoding="utf-8")
    assert token not in stored
    state = json.loads(stored)
    assert state["credentials"][credential_id]["token_hash"]
    assert pairing["device_secret"] not in stored


def test_agent_bearer_mutation_uses_rbac_without_browser_csrf(tmp_path):
    with TestClient(create_app(agent_settings(tmp_path))) as client:
        admin_headers = login(client)
        _, token, _ = pair_agent(client, admin_headers)
        bearer = {"Authorization": f"Bearer {token}"}

        response = client.post("/api/runs", headers=bearer, json={})
        assert response.status_code == 422
        assert response.json()["detail"] != "CSRF validation failed"


def test_pairing_requires_browser_approval_and_rejects_wrong_secret(tmp_path):
    with TestClient(create_app(agent_settings(tmp_path))) as client:
        started = client.post(
            "/api/auth/agent-pairings/start", json={"label": "Windows test host"}
        ).json()
        pending = client.post(
            f"/api/auth/agent-pairings/{started['pairing_id']}/exchange",
            json={"device_secret": started["device_secret"]},
        )
        assert pending.json()["status"] == "pending"
        assert client.get("/api/auth/agent-pairings").status_code == 401
        assert client.post(
            f"/api/auth/agent-pairings/{started['pairing_id']}/exchange",
            json={"device_secret": "x" * 40},
        ).status_code == 401


def test_agent_access_fails_closed_when_not_configured(tmp_path):
    settings = agent_settings(tmp_path).model_copy(update={"agent_access_file": ""})
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/agent-pairings/start", json={"label": "Codex client"}
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "agent access is not configured"


def test_connection_helper_writes_private_token_reference_without_embedding_secret(tmp_path):
    spec = importlib.util.spec_from_file_location("taskhub_agent_connect", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token_file = tmp_path / "private" / "agent-token"
    connection_file = tmp_path / "connection.json"
    connection_file.write_text(
        json.dumps({"base_url": "https://taskhub.test:8200"}), encoding="utf-8"
    )

    module.atomic_private_write(token_file, "secret-agent-token")
    module.update_connection(connection_file, token_file)

    connection = json.loads(connection_file.read_text(encoding="utf-8"))
    assert connection["api_auth"] == "bearer-token"
    assert connection["agent_token_file"] == str(token_file.resolve())
    assert "secret-agent-token" not in connection_file.read_text(encoding="utf-8")
    assert token_file.stat().st_mode & 0o077 == 0


def test_connection_check_uses_declared_urls_tls_direct_mode_and_bearer(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("taskhub_agent_check", CHECK_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ca_file = tmp_path / "taskhub.crt"
    ca_file.write_text("test certificate", encoding="utf-8")
    token_file = tmp_path / "agent-token"
    token_file.write_text("private-token\n", encoding="utf-8")
    connection_file = tmp_path / "connection.json"
    connection_file.write_text(
        json.dumps(
            {
                "base_url": "https://taskhub.test:8200",
                "health_url": "https://taskhub.test:8200/api/health",
                "auth_status_url": "https://taskhub.test:8200/api/auth/status",
                "tls_ca_file": str(ca_file),
                "proxy_mode": "direct",
                "api_auth": "bearer-token",
                "agent_token_file": str(token_file),
            }
        ),
        encoding="utf-8",
    )
    observed = {"urls": []}

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size=-1):
            if not self.payload:
                return b""
            if size < 0:
                result, self.payload = self.payload, b""
                return result
            result, self.payload = self.payload[:size], self.payload[size:]
            return result

    class Opener:
        def open(self, request, timeout):
            url = request.full_url
            observed["urls"].append(url)
            observed["authorization"] = request.get_header("Authorization")
            observed["timeout"] = timeout
            if url.endswith("/api/health"):
                return Response({"status": "ok"})
            return Response({"authenticated": True, "role": "project_owner"})

    def fake_build_http_opener(ca_path, trust_env):
        observed["ca_file"] = ca_path
        observed["trust_env"] = trust_env
        return Opener()

    monkeypatch.setattr(module, "build_http_opener", fake_build_http_opener)
    result = module.check_connection(connection_file, 10)

    assert observed["urls"] == [
        "https://taskhub.test:8200/api/health",
        "https://taskhub.test:8200/api/auth/status",
    ]
    assert observed["ca_file"] == str(ca_file)
    assert observed["trust_env"] is False
    assert observed["authorization"] == "Bearer private-token"
    assert result == {
        "connection": "present",
        "health": "ok",
        "authentication": "authenticated",
        "role": "project_owner",
    }


def test_connection_check_refuses_to_guess_health_path(tmp_path):
    spec = importlib.util.spec_from_file_location("taskhub_agent_check_missing", CHECK_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection_file = tmp_path / "connection.json"
    connection_file.write_text(
        json.dumps({"base_url": "https://taskhub.test:8200"}), encoding="utf-8"
    )

    try:
        module.load_connection(connection_file)
    except ValueError as error:
        assert "must not be guessed" in str(error)
    else:
        raise AssertionError("missing health_url should fail closed")


def test_console_loads_agent_access_as_separate_feature_asset(tmp_path):
    with TestClient(create_app(agent_settings(tmp_path))) as client:
        html = client.get("/").text
        script = client.get("/static/agent-access.js").text
    assert "/static/agent-access.css?v=2" in html
    assert "/static/agent-access.js?v=2" in html
    assert "开发代理连接" in script
    assert "setInterval(loadAgentAccess, 2000)" in script
