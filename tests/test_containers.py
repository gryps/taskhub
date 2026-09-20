import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.security.encryption import SecretCipher
from taskhub_v2.security.node_credentials import NodeCredentialVault
from taskhub_v2.services.containers import ContainerCreate, ContainerManager


class FakeDockerClient:
    def __init__(self):
        self.calls = []
        self.containers = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/version":
            return 200, {"Version": "28.0-test"}
        if path == "/info":
            return 200, {
                "OperatingSystem": "TaskHub Test Linux",
                "Architecture": "x86_64",
                "NCPU": 4,
                "MemTotal": 8 * 1024**3,
                "DockerRootDir": "/var/lib/docker",
            }
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


def manager(tmp_path, client=None, credentials=None):
    return ContainerManager(
        enabled=True,
        socket_path="/unused/docker.sock",
        network="taskhub-test_default",
        image="taskhub:test",
        node_token="node-secret",
        nodes_file=str(tmp_path / "nodes.json"),
        credentials=credentials,
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


def test_execution_container_enables_coding_sandbox_and_account_mount(tmp_path):
    docker = FakeDockerClient()
    subject = ContainerManager(
        enabled=True,
        socket_path="/unused/docker.sock",
        network="taskhub-test_default",
        image="taskhub:test",
        node_token="node-secret",
        nodes_file=str(tmp_path / "nodes.json"),
        data_volume_name="taskhub-data",
        model_accounts_volume_subpath="config/model-accounts",
        openai_proxy_url="http://proxy.test:7890",
        client=docker,
    )

    subject.create(ContainerCreate(node_id="work-01", role="execution", slots=1))

    payload = next(call[2] for call in docker.calls if call[1].startswith("/containers/create"))
    assert "TASKHUB_NODE_CODING_ENABLED=true" in payload["Env"]
    assert (
        "TASKHUB_MODEL_CARDS_FILE=/var/lib/taskhub-node/codex/accounts/node-models.json"
        in payload["Env"]
    )
    assert payload["HostConfig"]["SecurityOpt"] == ["seccomp=unconfined"]
    assert payload["HostConfig"]["Mounts"] == [
        {
            "Type": "volume",
            "Source": "taskhub-data",
            "Target": "/var/lib/taskhub-node/codex/accounts",
            "ReadOnly": False,
            "VolumeOptions": {"Subpath": "config/model-accounts"},
        }
    ]


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


def test_local_container_uses_rotatable_per_node_credential(tmp_path):
    credentials = NodeCredentialVault(
        str(tmp_path / "credentials.json"),
        SecretCipher(Fernet.generate_key().decode()),
    )
    docker = FakeDockerClient()
    subject = manager(tmp_path, docker, credentials)
    subject.create(ContainerCreate(node_id="test-01", role="test", slots=2))
    original = credentials.resolve("test-01")

    rotated = subject.action("test-01", "rotate-credential")

    assert credentials.resolve("test-01") != original
    assert rotated["credential"]["version"] == 2
    assert subject.list()[0]["credential"]["status"] == "active"
    subject.action("test-01", "remove")
    assert credentials.resolve("test-01") == ""


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
