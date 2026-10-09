from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import BinaryIO
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from taskhub_v2.services.container_diagnostics import container_resources, docker_log_text
from taskhub_v2.services.docker_engine import DockerSocketClient, DockerUnavailableError
from taskhub_v2.services.local_node_lifecycle import (
    ROLE_WORKLOADS,
    ContainerCreate,
    DockerConflictError,
    LocalNodeLifecycleMixin,
)
from taskhub_v2.services.node_inventory import NodeInventoryMixin

__all__ = [
    "ContainerCreate",
    "ContainerManager",
    "DockerConflictError",
    "DockerUnavailableError",
    "ROLE_WORKLOADS",
]


class ContainerManager(LocalNodeLifecycleMixin, NodeInventoryMixin):
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
        data_volume_name: str = "",
        model_accounts_volume_subpath: str = "",
        openai_proxy_url: str = "",
        test_database_admin_dsn: str = "",
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
        self.data_volume_name = data_volume_name
        self.model_accounts_volume_subpath = model_accounts_volume_subpath.strip("/")
        self.openai_proxy_url = openai_proxy_url
        self.test_database_admin_dsn = test_database_admin_dsn
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
        for item in views:
            item["desired_image"] = self.image
            item["upgrade_available"] = bool(
                self.image and item["image"] and item["image"] != self.image
            )
            if self.credentials:
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
            messages = self.client.upload("/images/load?quiet=0", source, archive.stat().st_size)
        metadata = self.image_metadata(image)
        detail = next(
            (item.get("stream", "").strip() for item in reversed(messages) if item.get("stream")),
            "镜像已导入 Seed Docker Engine",
        )
        return {**metadata, "image": image, "size_bytes": archive.stat().st_size, "detail": detail}
