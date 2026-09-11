import json

from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings


def secure_settings(tmp_path, **updates):
    values = {
        "checkpointer": "memory",
        "admin_token": "admin-secret",
        "session_secret": "session-secret",
        "users_file": str(tmp_path / "users.json"),
        "session_state_file": str(tmp_path / "sessions.json"),
        "session_signing_keys_file": str(tmp_path / "signing-keys.json"),
        "operations_log_file": str(tmp_path / "operations.jsonl"),
    }
    values.update(updates)
    return Settings(**values)


def login(client, username="admin", password="admin-secret"):
    response = client.post("/api/auth/login", json={"username": username, "token": password})
    headers = {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf", "")}
    return response, headers


def test_user_roles_are_persisted_and_enforced(tmp_path):
    app = create_app(secure_settings(tmp_path))
    with TestClient(app) as client:
        response, headers = login(client)
        assert response.status_code == 200
        created = client.put(
            "/api/auth/users/dev-one",
            headers=headers,
            json={
                "username": "dev-one",
                "role": "developer",
                "password": "developer-password",
                "enabled": True,
            },
        )
        assert created.status_code == 200
        client.post("/api/auth/logout", headers=headers)

        response, dev_headers = login(client, "dev-one", "developer-password")
        assert response.status_code == 200
        status = client.get("/api/auth/status").json()
        assert status["actor"] == "dev-one"
        assert status["role"] == "developer"
        assert client.get("/api/projects").status_code == 200
        assert client.put("/api/settings/platform", headers=dev_headers, json={}).status_code == 403
        assert client.get("/api/auth/users").status_code == 403

    stored = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    assert "developer-password" not in str(stored)


def test_logout_revokes_server_side_session(tmp_path):
    with TestClient(create_app(secure_settings(tmp_path))) as client:
        _, headers = login(client)
        cookie = client.cookies.get("taskhub_v2_session")
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        client.cookies.set("taskhub_v2_session", cookie)
        assert client.get("/api/projects").status_code == 401


def test_login_failure_limit_and_security_headers(tmp_path):
    settings = secure_settings(tmp_path, login_max_failures=3, login_lock_seconds=60)
    with TestClient(create_app(settings)) as client:
        for _ in range(2):
            assert (
                client.post(
                    "/api/auth/login", json={"username": "admin", "token": "wrong"}
                ).status_code
                == 401
            )
        third = client.post("/api/auth/login", json={"username": "admin", "token": "wrong"})
        assert third.status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "token": "admin-secret"}
            ).status_code
            == 429
        )
        health = client.get("/api/health")
        assert health.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in health.headers["content-security-policy"]


def test_signing_key_rotation_revokes_all_sessions(tmp_path):
    with TestClient(create_app(secure_settings(tmp_path))) as client:
        _, headers = login(client)
        response = client.post("/api/auth/signing-key/rotate", headers=headers)
        assert response.status_code == 200
        assert response.json()["rotated"] is True
        assert client.get("/api/projects").status_code == 401
        keys = json.loads((tmp_path / "signing-keys.json").read_text(encoding="utf-8"))
        assert len(keys["keys"]) == 2


def test_idle_timeout_and_secure_cookie(tmp_path):
    settings = secure_settings(
        tmp_path, cookie_secure=True, session_idle_seconds=60, session_absolute_seconds=600
    )
    with TestClient(create_app(settings), base_url="https://taskhub.test") as client:
        response, _ = login(client)
        session_cookie = next(
            item for item in response.headers.get_list("set-cookie") if "taskhub_v2_session" in item
        )
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "SameSite=strict" in session_cookie
        state_path = tmp_path / "sessions.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for item in state["sessions"].values():
            item["last_seen"] = 1
        state_path.write_text(json.dumps(state), encoding="utf-8")
        assert client.get("/api/projects").status_code == 401
