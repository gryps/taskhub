from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import BinaryIO
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from taskhub_v2.domain.models import NodeDefinition
from taskhub_v2.security.node_credentials import NodeCredentialError
from taskhub_v2.services.container_diagnostics import container_resources, docker_log_text
from taskhub_v2.services.docker_engine import DockerSocketClient, DockerUnavailableError
from taskhub_v2.services.node_inventory import NodeInventoryMixin

ROLE_WORKLOADS = {
    "execution": {"build", "coding"},
    "test": {"acceptance", "test"},
    "preproduction": {"acceptance", "build", "test"},
}


class ContainerCreate(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    role: str = Field(pattern=r"^(execution|test|preproduction)$")
    slots: int = Field(default=1, ge=1, le=16)


class DockerConflictError(RuntimeError):
    pass


class ContainerManager(NodeInventoryMixin):
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
        credentials=None,
        client=None,
        operation_log=None,
    ):
        self.enabled = enabled
        self.network = network
        self.image = image
        self.node_token = node_token
        self.credentials = credentials
        self.nodes_file = Path(nodes_file)
        self.client = client or DockerSocketClient(socket_path)
        self.operation_log = operation_log
        self.lock = RLock()

    def _require_available(self):
        if not self.enabled:
            raise DockerUnavailableError("当前部署未启用 Web 容器管理")
        if not self.node_token and not self.credentials:
            raise DockerUnavailableError("尚未配置节点通信令牌")

    def status(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "available": False, "detail": "当前部署未启用"}
        if not self.node_token and not self.credentials:
            return {"enabled": True, "available": False, "detail": "尚未配置节点通信令牌"}
        status, version = self.client.request("GET", "/version")
        if status != 200:
            return {
                "enabled": True,
                "available": False,
                "detail": version.get("message", "Docker Engine 不可用"),
            }
        info_status, info = self.client.request("GET", "/info")
        if info_status != 200:
            info = {}
        return {
            "enabled": True,
            "available": True,
            "detail": f"Docker Engine {version.get('Version', '可用')}",
            "image": self.image,
            "network": self.network,
            "engine": {
                "version": version.get("Version", ""),
                "operating_system": info.get("OperatingSystem", ""),
                "architecture": info.get("Architecture", ""),
                "cpu_count": info.get("NCPU"),
                "memory_total_bytes": info.get("MemTotal"),
                "docker_root": info.get("DockerRootDir", ""),
            },
        }

    def list(self) -> list[dict]:
        self._require_available()
        filters = json.dumps({"label": [f"{self.label}=true"]})
        status, data = self.client.request(
            "GET", f"/containers/json?{urlencode({'all': '1', 'filters': filters})}"
        )
        if status != 200:
            raise DockerUnavailableError(data.get("message", "无法读取容器"))
        views = [self._view(item) for item in data]
        if self.credentials:
            for item in views:
                item["credential"] = self.credentials.metadata(item["node_id"])
        return views

    def diagnostics(self, node_id: str, tail: int = 200) -> dict:
        self._require_available()
        container = self._find(node_id)
        container_id = container["Id"]
        status, raw = self.client.request_bytes(
            "GET",
            f"/containers/{quote(container_id)}/logs?"
            f"stdout=1&stderr=1&timestamps=1&tail={max(1, min(tail, 500))}",
        )
        if status != 200:
            raise DockerUnavailableError("无法读取容器日志")
        stat_status, stats = self.client.request(
            "GET", f"/containers/{quote(container_id)}/stats?stream=false"
        )
        if stat_status != 200:
            stats = {}
        result = {
            "container_logs": docker_log_text(raw),
            "container": self._view(container),
            "resources": container_resources(stats),
        }
        try:
            token = self.credentials.resolve(node_id) if self.credentials else self.node_token
            name = result["container"]["name"]
            request = Request(
                f"http://{name}:8020/api/diagnostics",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=5) as response:  # noqa: S310 - internal Docker DNS
                agent = json.loads(response.read())
            result.update(agent)
        except Exception as exc:
            result["agent_error"] = str(exc)[:500]
        return result

    def create(self, request: ContainerCreate, *, _token: str | None = None) -> dict:
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
                f"TASKHUB_NODE_ROLE={request.role}",
                f"TASKHUB_NODE_SLOTS={request.slots}",
                "TASKHUB_NODE_TOKEN=__TASKHUB_NODE_CREDENTIAL__",
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
            try:
                token = _token or (
                    self.credentials.issue(request.node_id)
                    if self.credentials
                    else self.node_token
                )
            except NodeCredentialError as exc:
                raise DockerUnavailableError(str(exc)) from exc
            payload["Env"] = [
                item.replace("__TASKHUB_NODE_CREDENTIAL__", token)
                for item in payload["Env"]
            ]
            try:
                status, data = self.client.request(
                    "POST", f"/containers/create?name={quote(name)}", payload
                )
            except Exception:
                if self.credentials:
                    self.credentials.revoke(request.node_id)
                raise
            if status == 409:
                if self.credentials:
                    self.credentials.revoke(request.node_id)
                raise DockerConflictError(data.get("message", "同名容器已存在"))
            if status != 201:
                if self.credentials:
                    self.credentials.revoke(request.node_id)
                raise DockerUnavailableError(data.get("message", "容器创建失败"))
            container_id = data["Id"]
            status, start_data = self.client.request(
                "POST", f"/containers/{quote(container_id)}/start"
            )
            if status not in {204, 304}:
                self.client.request("DELETE", f"/containers/{quote(container_id)}?force=true")
                if self.credentials:
                    self.credentials.revoke(request.node_id)
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
                if self.credentials:
                    self.credentials.revoke(request.node_id)
                raise
        if self.operation_log:
            self.operation_log.record(
                "local_docker_create", "passed", node_id=request.node_id,
                role=request.role,
            )
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
        if action == "rotate-credential":
            return self.rotate_credential(node_id)
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
            if self.credentials:
                self.credentials.revoke(node_id)
        if self.operation_log:
            self.operation_log.record(
                "local_docker_action", "passed", node_id=node_id, action=action
            )
        return {"node_id": node_id, "action": action, "ok": True}

    def rotate_credential(self, node_id: str) -> dict:
        if not self.credentials:
            raise DockerUnavailableError("当前节点仍使用旧版部署级令牌，无法独立轮换")
        with self.lock:
            container = self._find(node_id)
            node = next(
                (item for item in self._registered_nodes() if item.id == node_id), None
            )
            if not node:
                raise DockerUnavailableError("节点调度记录不存在，无法安全轮换")
            role = (container.get("Labels") or {}).get("io.taskhub.role", "")
            try:
                token = self.credentials.rotate(node_id)
            except NodeCredentialError as exc:
                raise DockerUnavailableError(str(exc)) from exc
            status, data = self.client.request(
                "DELETE", f"/containers/{container['Id']}?force=true"
            )
            if status != 204:
                raise DockerUnavailableError(data.get("message", "旧节点容器移除失败"))
            self._unregister(node_id)
            result = self.create(
                ContainerCreate(node_id=node_id, role=role, slots=node.slots),
                _token=token,
            )
        return {**result, "credential": self.credentials.metadata(node_id)}

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

    def image_metadata(self, image: str) -> dict:
        self._require_available()
        status, data = self.client.request("GET", f"/images/{quote(image, safe='')}/json")
        if status == 404:
            raise DockerUnavailableError(f"Seed 本机不存在工作节点镜像 {image}")
        if status != 200:
            raise DockerUnavailableError(data.get("message", "无法读取本机镜像信息"))
        digest = next(iter(data.get("RepoDigests") or []), data.get("Id", ""))
        return {
            "id": data.get("Id", ""),
            "digest": digest,
            "architecture": data.get("Architecture", ""),
            "os": data.get("Os", ""),
        }

    def export_image(self, image: str, destination: BinaryIO) -> dict:
        metadata = self.image_metadata(image)
        self.client.download(f"/images/{quote(image, safe='')}/get", destination)
        destination.flush()
        return metadata

    def import_image(self, archive: Path, image: str) -> dict:
        self._require_available()
        with archive.open("rb") as source:
            messages = self.client.upload(
                "/images/load?quiet=0", source, archive.stat().st_size
            )
        metadata = self.image_metadata(image)
        detail = next(
            (item.get("stream", "").strip() for item in reversed(messages)
             if item.get("stream")),
            "镜像已导入 Seed Docker Engine",
        )
        return {**metadata, "image": image, "size_bytes": archive.stat().st_size,
                "detail": detail}
