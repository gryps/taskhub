from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field

from taskhub_v2.domain.models import NodeDefinition

ROLE_WORKLOADS = {
    "execution": {"build", "coding"},
    "test": {"acceptance", "test"},
    "preproduction": {"acceptance", "build", "test"},
}


class ContainerCreate(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    role: str = Field(pattern=r"^(execution|test|preproduction)$")
    slots: int = Field(default=1, ge=1, le=16)


class DockerUnavailableError(RuntimeError):
    pass


class DockerConflictError(RuntimeError):
    pass


class UnixSocketConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


class DockerSocketClient:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        connection = UnixSocketConnection(self.socket_path)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailableError(f"Docker Engine 不可用：{exc}") from exc
        finally:
            connection.close()
        data = json.loads(raw) if raw else {}
        return response.status, data


class ContainerManager:
    label = "io.taskhub.managed"

    def __init__(
        self,
        *,
        enabled: bool,
        socket_path: str,
        network: str,
        image: str,
        node_token: str,
        nodes_file: str,
        client=None,
    ):
        self.enabled = enabled
        self.network = network
        self.image = image
        self.node_token = node_token
        self.nodes_file = Path(nodes_file)
        self.client = client or DockerSocketClient(socket_path)
        self.lock = Lock()

    def _require_available(self):
        if not self.enabled:
            raise DockerUnavailableError("当前部署未启用 Web 容器管理")
        if not self.node_token:
            raise DockerUnavailableError("尚未配置节点通信令牌")

    def status(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "available": False, "detail": "当前部署未启用"}
        if not self.node_token:
            return {"enabled": True, "available": False, "detail": "尚未配置节点通信令牌"}
        status, data = self.client.request("GET", "/version")
        if status != 200:
            return {
                "enabled": True,
                "available": False,
                "detail": data.get("message", "Docker Engine 不可用"),
            }
        return {
            "enabled": True,
            "available": True,
            "detail": f"Docker Engine {data.get('Version', '可用')}",
            "image": self.image,
            "network": self.network,
        }

    def list(self) -> list[dict]:
        self._require_available()
        filters = json.dumps({"label": [f"{self.label}=true"]})
        status, data = self.client.request(
            "GET", f"/containers/json?{urlencode({'all': '1', 'filters': filters})}"
        )
        if status != 200:
            raise DockerUnavailableError(data.get("message", "无法读取容器"))
        return [self._view(item) for item in data]

    def create(self, request: ContainerCreate) -> dict:
        self._require_available()
        name = f"taskhub-node-{request.node_id}"
        workloads = ROLE_WORKLOADS[request.role]
        node_command = (
            "mkdir -p /var/lib/taskhub-node/jobs; "
            "chown -R taskhub:taskhub /var/lib/taskhub-node; "
            "exec runuser -u taskhub -- python -m uvicorn "
            "taskhub_v2.node_agent:create_node_app --factory --host 0.0.0.0 --port 8020"
        )
        payload = {
            "Image": self.image,
            "User": "0:0",
            "Labels": {
                self.label: "true",
                "io.taskhub.node-id": request.node_id,
                "io.taskhub.role": request.role,
            },
            "Env": [
                f"TASKHUB_NODE_ID={request.node_id}",
                f"TASKHUB_NODE_TOKEN={self.node_token}",
                "TASKHUB_NODE_WORK_ROOT=/var/lib/taskhub-node/jobs",
            ],
            "Cmd": ["sh", "-c", node_command],
            "Healthcheck": {
                "Test": [
                    "CMD",
                    "python",
                    "-c",
                    "import os,urllib.request; "
                    "q=urllib.request.Request('http://127.0.0.1:8020/api/health',"
                    "headers={'Authorization':'Bearer '+os.environ['TASKHUB_NODE_TOKEN']}); "
                    "urllib.request.urlopen(q,timeout=2).read()",
                ],
                "Interval": 10_000_000_000,
                "Timeout": 3_000_000_000,
                "StartPeriod": 20_000_000_000,
                "Retries": 6,
            },
            "HostConfig": {
                "Binds": [f"{name}-data:/var/lib/taskhub-node"],
                "NetworkMode": self.network,
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }
        with self.lock:
            if any(node.id == request.node_id for node in self._registered_nodes()):
                raise DockerConflictError("节点 ID 已存在")
            status, data = self.client.request(
                "POST", f"/containers/create?name={quote(name)}", payload
            )
            if status == 409:
                raise DockerConflictError(data.get("message", "同名容器已存在"))
            if status != 201:
                raise DockerUnavailableError(data.get("message", "容器创建失败"))
            container_id = data["Id"]
            status, start_data = self.client.request(
                "POST", f"/containers/{quote(container_id)}/start"
            )
            if status not in {204, 304}:
                self.client.request("DELETE", f"/containers/{quote(container_id)}?force=true")
                raise DockerUnavailableError(start_data.get("message", "容器启动失败"))
            try:
                self._register(
                    NodeDefinition(
                        id=request.node_id,
                        kind="remote",
                        url=f"http://{name}:8020",
                        slots=request.slots,
                        workloads=workloads,
                    )
                )
            except Exception:
                self.client.request("DELETE", f"/containers/{quote(container_id)}?force=true")
                raise
        return {
            "id": container_id,
            "name": name,
            "node_id": request.node_id,
            "role": request.role,
            "state": "running",
            "slots": request.slots,
            "workloads": sorted(workloads),
        }

    def action(self, node_id: str, action: str) -> dict:
        self._require_available()
        container = self._find(node_id)
        container_id = container["Id"]
        if action == "start":
            status, data = self.client.request("POST", f"/containers/{container_id}/start")
            expected = {204, 304}
        elif action == "stop":
            status, data = self.client.request("POST", f"/containers/{container_id}/stop?t=10")
            expected = {204, 304}
        elif action == "remove":
            status, data = self.client.request("DELETE", f"/containers/{container_id}?force=true")
            expected = {204}
        else:
            raise ValueError("不支持的容器操作")
        if status not in expected:
            raise DockerUnavailableError(data.get("message", "容器操作失败"))
        if action == "remove":
            with self.lock:
                self._unregister(node_id)
        return {"node_id": node_id, "action": action, "ok": True}

    def _find(self, node_id: str) -> dict:
        filters = json.dumps({"label": [f"{self.label}=true", f"io.taskhub.node-id={node_id}"]})
        status, data = self.client.request(
            "GET", f"/containers/json?{urlencode({'all': '1', 'filters': filters})}"
        )
        if status != 200:
            raise DockerUnavailableError(data.get("message", "无法读取容器"))
        if not data:
            raise KeyError(node_id)
        return data[0]

    @staticmethod
    def _view(item: dict) -> dict:
        labels = item.get("Labels") or {}
        names = item.get("Names") or []
        return {
            "id": item.get("Id", ""),
            "name": names[0].removeprefix("/") if names else "",
            "node_id": labels.get("io.taskhub.node-id", ""),
            "role": labels.get("io.taskhub.role", ""),
            "state": item.get("State", "unknown"),
            "status": item.get("Status", ""),
            "image": item.get("Image", ""),
        }

    def _registered_nodes(self) -> list[NodeDefinition]:
        if not self.nodes_file.is_file():
            return []
        payload = json.loads(self.nodes_file.read_text(encoding="utf-8"))
        return [NodeDefinition.model_validate(item) for item in payload.get("nodes", [])]

    def _write_nodes(self, nodes: list[NodeDefinition]):
        self.nodes_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.nodes_file.with_suffix(".tmp")
        payload = {
            "nodes": [
                {**node.model_dump(mode="json"), "workloads": sorted(node.workloads)}
                for node in nodes
            ]
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.nodes_file)

    def _register(self, node: NodeDefinition):
        nodes = self._registered_nodes()
        nodes.append(node)
        self._write_nodes(nodes)

    def _unregister(self, node_id: str):
        self._write_nodes([node for node in self._registered_nodes() if node.id != node_id])
