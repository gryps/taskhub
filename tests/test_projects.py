import subprocess
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ProjectDefinition
from taskhub_v2.projects import ProjectRegistry


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def repository(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@taskhub.local")
    git(path, "config", "user.name", "TaskHub Test")
    (path / "README.md").write_text("test\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


class FakeProvisioner:
    def __init__(self, registry, repository_path: Path):
        self.registry = registry
        self.repository_path = repository_path

    async def create(
        self, name, project_id, base_ref, test_commands, acceptance_commands=None,
        acceptance_capabilities=None,
    ):
        return self.registry.add(
            ProjectDefinition(
                id=project_id,
                name=name,
                repository=str(self.repository_path),
                authority_remote="origin",
                base_ref=base_ref,
                test_commands=test_commands,
                acceptance_commands=acceptance_commands or [],
                acceptance_capabilities=acceptance_capabilities or set(),
            )
        )

    async def available(self):
        return [
            {
                "repository": "shop.git",
                "name": "shop",
                "default_branch": "main",
                "attached": False,
                "project_id": "shop",
            }
        ]

    async def attach(
        self, repository, name, base_ref, test_commands, acceptance_commands=None,
        acceptance_capabilities=None,
    ):
        assert repository == "shop.git"
        return self.registry.add(
            ProjectDefinition(
                id="shop",
                name=name,
                repository=str(self.repository_path),
                authority_remote="origin",
                base_ref=base_ref,
                test_commands=test_commands,
                acceptance_commands=acceptance_commands or [],
                acceptance_capabilities=acceptance_capabilities or set(),
            )
        )


def test_new_project_creation_uses_the_provisioner(tmp_path: Path):
    projects_file = tmp_path / "projects.json"
    repo = repository(tmp_path / "created")
    app = create_app(
        Settings(
            admin_token="admin-secret",
            session_secret="session-secret",
            projects_file=str(projects_file),
        )
    )
    app.state.project_provisioner = FakeProvisioner(app.state.projects, repo)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"token": "admin-secret"})
        response = client.post(
            "/api/projects",
            headers={"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]},
            json={
                "name": "New Shop",
                "project_id": "new-shop",
                "base_ref": "main",
                "test_commands": "npm test",
                "acceptance_commands": "python3 accept.py",
            },
        )

    assert response.status_code == 201
    assert response.json()["id"] == "new-shop"
    assert response.json()["name"] == "New Shop"
    assert response.json()["test_commands"] == [["npm", "test"]]
    assert response.json()["acceptance_commands"] == [["python3", "accept.py"]]


def test_authority_project_can_be_selected_and_attached_from_the_api(tmp_path: Path):
    projects_file = tmp_path / "projects.json"
    app = create_app(
        Settings(
            admin_token="admin-secret",
            session_secret="session-secret",
            projects_file=str(projects_file),
        )
    )
    repo = repository(tmp_path / "shop")
    app.state.project_provisioner = FakeProvisioner(app.state.projects, repo)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"token": "admin-secret"})
        headers = {"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]}
        available = client.get("/api/projects/available")
        response = client.post(
            "/api/projects/attach",
            headers=headers,
            json={
                "name": "Shop Platform",
                "repository": "shop.git",
                "base_ref": "main",
                "test_commands": "python3 -m pytest -q\nnpm test",
            },
        )
        listed = client.get("/api/projects")

    assert response.status_code == 201
    assert available.status_code == 200
    assert available.json()["repositories"][0]["repository"] == "shop.git"
    assert response.json()["name"] == "Shop Platform"
    assert listed.json()["projects"][0]["repository"] == str(repo)
    assert listed.json()["projects"][0]["test_commands"] == [
        ["python3", "-m", "pytest", "-q"],
        ["npm", "test"],
    ]
    assert projects_file.stat().st_mode & 0o777 == 0o600


def test_project_registration_rejects_an_invalid_authority_repository(tmp_path: Path):
    app = create_app(
        Settings(
            admin_token="admin-secret",
            session_secret="session-secret",
            projects_file=str(tmp_path / "projects.json"),
        )
    )

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"token": "admin-secret"})
        response = client.post(
            "/api/projects/attach",
            headers={"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]},
            json={"name": "Invalid", "repository": "../invalid.git"},
        )

    assert response.status_code == 422


def test_project_test_environment_can_be_configured(tmp_path: Path):
    projects_file = tmp_path / "projects.json"
    repo = repository(tmp_path / "shop")
    app = create_app(
        Settings(
            admin_token="admin-secret",
            session_secret="session-secret",
            projects_file=str(projects_file),
        )
    )
    app.state.projects.add(ProjectDefinition(id="shop", repository=str(repo)))

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"token": "admin-secret"})
        response = client.put(
            "/api/projects/shop/test-environment",
            headers={"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]},
            json={
                "target_url": "https://192.168.31.55/",
                "edge_host": "192.168.31.55",
                "origin_host": "192.168.31.56",
                "expected_environment": "production",
            },
        )

    assert response.status_code == 200
    assert response.json()["test_environment"] == {
        "profile": "dedicated",
        "target_url": "https://192.168.31.55",
        "edge_host": "192.168.31.55",
        "origin_host": "192.168.31.56",
        "expected_environment": "production",
    }
    assert ProjectRegistry(str(projects_file)).get("shop").test_environment is not None


def test_project_test_environment_can_be_disabled(tmp_path: Path):
    projects_file = tmp_path / "projects.json"
    repo = repository(tmp_path / "shop")
    app = create_app(Settings(admin_token="admin-secret", session_secret="session-secret",
                              projects_file=str(projects_file)))
    app.state.projects.add(ProjectDefinition(
        id="shop", repository=str(repo), test_environment={
            "target_url": "https://preprod.example.com",
            "edge_host": "edge.example.com",
            "origin_host": "origin.example.com",
        },
    ))

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"token": "admin-secret"})
        response = client.delete(
            "/api/projects/shop/test-environment",
            headers={"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]},
        )

    assert response.status_code == 200
    assert response.json()["test_environment"] is None
    assert ProjectRegistry(str(projects_file)).get("shop").test_environment is None


def test_project_test_environment_can_be_checked(tmp_path: Path, monkeypatch):
    projects_file = tmp_path / "projects.json"
    repo = repository(tmp_path / "shop")
    app = create_app(Settings(admin_token="admin-secret", session_secret="session-secret",
                              projects_file=str(projects_file)))
    app.state.projects.add(ProjectDefinition(
        id="shop", repository=str(repo), test_environment={
            "target_url": "https://preprod.example.com",
            "edge_host": "edge.example.com",
            "origin_host": "origin.example.com",
        },
    ))

    async def successful_get(self, url):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", successful_get)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"token": "admin-secret"})
        response = client.post(
            "/api/projects/shop/test-environment/check",
            headers={"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "status_code": 200,
        "detail": "验收入口已响应 HTTP 200",
    }
