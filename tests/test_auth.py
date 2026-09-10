import json

from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings


def password_settings(password_file) -> Settings:
    return Settings(
        checkpointer="memory",
        admin_token="bootstrap-secret",
        admin_password_file=str(password_file),
        session_secret="session-secret",
    )


def test_first_login_sets_durable_password_and_creates_session(tmp_path):
    password_file = tmp_path / "config" / "admin-password.json"
    app = create_app(password_settings(password_file))

    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status == {
            "authenticated": False,
            "configured": True,
            "password_login": True,
            "setup_required": True,
        }
        assert client.post(
            "/api/auth/login", json={"token": "bootstrap-secret"}
        ).status_code == 409
        assert client.post(
            "/api/auth/setup",
            json={"bootstrap_token": "wrong", "password": "durable-password"},
        ).status_code == 401

        response = client.post(
            "/api/auth/setup",
            json={
                "bootstrap_token": "bootstrap-secret",
                "password": "durable-password",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"authenticated": True}
        assert client.cookies.get("taskhub_v2_session")
        assert client.cookies.get("taskhub_v2_csrf")

    record_text = password_file.read_text(encoding="utf-8")
    record = json.loads(record_text)
    assert record["scheme"] == "scrypt"
    assert record["salt"] and record["hash"]
    assert "durable-password" not in record_text
    assert "bootstrap-secret" not in record_text


def test_password_replaces_bootstrap_token_for_subsequent_login(tmp_path):
    password_file = tmp_path / "admin-password.json"
    app = create_app(password_settings(password_file))

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/setup",
            json={
                "bootstrap_token": "bootstrap-secret",
                "password": "durable-password",
            },
        ).status_code == 200

    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["setup_required"] is False
        assert status["authenticated"] is False
        assert client.post(
            "/api/auth/login", json={"token": "bootstrap-secret"}
        ).status_code == 401
        assert client.post(
            "/api/auth/login", json={"token": "durable-password"}
        ).status_code == 200
        assert client.post(
            "/api/auth/setup",
            json={
                "bootstrap_token": "bootstrap-secret",
                "password": "another-password",
            },
        ).status_code == 409


def test_password_setup_enforces_minimum_length(tmp_path):
    app = create_app(password_settings(tmp_path / "admin-password.json"))
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/setup",
            json={"bootstrap_token": "bootstrap-secret", "password": "short"},
        )
    assert response.status_code == 422


def test_invalid_password_record_fails_closed(tmp_path):
    password_file = tmp_path / "admin-password.json"
    password_file.write_text("[]", encoding="utf-8")
    app = create_app(password_settings(password_file))
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["setup_required"] is False
        assert client.post(
            "/api/auth/login", json={"token": "bootstrap-secret"}
        ).status_code == 401


def test_legacy_token_login_remains_available_without_password_file():
    app = create_app(
        Settings(
            checkpointer="memory",
            admin_token="admin-secret",
            session_secret="session-secret",
        )
    )
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["password_login"] is False
        assert status["setup_required"] is False
        assert client.post(
            "/api/auth/login", json={"token": "admin-secret"}
        ).status_code == 200
