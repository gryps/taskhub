import json

from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.services.containers import ContainerCreate, ContainerManager


class FakeDockerClient:
    def __init__(self):
        self.calls = []
        self.containers = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/version":
            return 200, {"Version": "28.0-test"}
        if method == "POST" and path.startswith("/containers/create?"):
            self.containers = [
                {
                    "Id": "container-1",
                    "Names": ["/taskhub-node-test-01"],
                    "Labels": {
                        "io.taskhub.managed": "true",
                        "io.taskhub.node-id": "test-01",
                        "io.taskhub.role": "test",
                    },
                    "State": "created",
                    "Status": "Created",
                    "Image": "taskhub:test",
                }
            ]
            return 201, {"Id": "container-1"}
        if method == "GET" and path.startswith("/containers/json?"):
            return 200, self.containers
        if method == "POST" and path.endswith("/start"):
            self.containers[0]["State"] = "running"
            self.containers[0]["Status"] = "Up"
            return 204, {}
        if method == "POST" and "/stop?" in path:
            self.containers[0]["State"] = "exited"
            self.containers[0]["Status"] = "Exited"
            return 204, {}
        if method == "DELETE":
            self.containers = []
            return 204, {}
        raise AssertionError((method, path, payload))


def manager(tmp_path, client=None):
    return ContainerManager(
        enabled=True,
        socket_path="/unused/docker.sock",
        network="taskhub-test_default",
        image="taskhub:test",
        node_token="node-secret",
        nodes_file=str(tmp_path / "nodes.json"),
        client=client or FakeDockerClient(),
    )


def test_container_manager_creates_registers_and_removes_node(tmp_path):
    docker = FakeDockerClient()
    subject = manager(tmp_path, docker)

    created = subject.create(ContainerCreate(node_id="test-01", role="test", slots=2))

    assert created["state"] == "running"
    assert created["workloads"] == ["acceptance", "test"]
    registry = json.loads((tmp_path / "nodes.json").read_text())
    assert registry["nodes"] == [
        {
            "id": "test-01",
            "kind": "remote",
            "url": "http://taskhub-node-test-01:8020",
            "slots": 2,
            "workloads": ["acceptance", "test"],
            "priority": 100,
            "enabled": True,
        }
    ]
    create_payload = next(
        call[2] for call in docker.calls if call[1].startswith("/containers/create")
    )
    assert create_payload["HostConfig"]["NetworkMode"] == "taskhub-test_default"
    assert create_payload["User"] == "0:0"
    assert "runuser -u taskhub" in create_payload["Cmd"][2]
    assert "Authorization" in create_payload["Healthcheck"]["Test"][3]
    assert all("node-secret" not in str(item) for item in subject.list())

    subject.action("test-01", "remove")
    assert json.loads((tmp_path / "nodes.json").read_text()) == {"nodes": []}


def test_container_status_is_disabled_without_runtime_authorization(tmp_path):
    subject = ContainerManager(
        enabled=False,
        socket_path="/unused/docker.sock",
        network="network",
        image="image",
        node_token="",
        nodes_file=str(tmp_path / "nodes.json"),
    )
    assert subject.status() == {
        "enabled": False,
        "available": False,
        "detail": "当前部署未启用",
    }


def test_container_routes_require_auth_and_csrf(tmp_path):
    settings = Settings(
        checkpointer="memory",
        admin_token="admin-secret",
        session_secret="session-secret",
    )
    app = create_app(settings)
    app.state.container_manager = manager(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/containers/status").status_code == 401
        client.post("/api/auth/login", json={"token": "admin-secret"})
        assert client.get("/api/containers/status").json()["available"] is True
        assert (
            client.post(
                "/api/containers",
                json={"node_id": "test-01", "role": "test", "slots": 1},
            ).status_code
            == 403
        )
        headers = {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf")}
        assert (
            client.post(
                "/api/containers",
                headers=headers,
                json={"node_id": "test-01", "role": "test", "slots": 1},
            ).status_code
            == 201
        )
