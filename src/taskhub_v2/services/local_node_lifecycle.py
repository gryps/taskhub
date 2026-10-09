from __future__ import annotations

import time
from urllib.parse import quote

from pydantic import BaseModel, Field

from taskhub_v2.domain.models import NodeDefinition
from taskhub_v2.security.node_credentials import NodeCredentialError
from taskhub_v2.services.container_runtime import node_runtime_configuration
from taskhub_v2.services.docker_engine import DockerUnavailableError


class ContainerCreate(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    role: str = Field(pattern=r"^(execution|test|preproduction)$")
    slots: int = Field(default=1, ge=1, le=16)


class DockerConflictError(RuntimeError):
    pass


ROLE_WORKLOADS = {
    "execution": {"build", "coding"},
    "test": {"acceptance", "test"},
    "preproduction": {"acceptance", "build", "test"},
}


class LocalNodeLifecycleMixin:
    node_upgrade_timeout_seconds = 75

    def create(
        self,
        request: ContainerCreate,
        *,
        _token: str | None = None,
        _image: str | None = None,
    ) -> dict:
        self._require_available()
        name = f"taskhub-node-{request.node_id}"
        workloads = ROLE_WORKLOADS[request.role]
        payload = self._container_payload(request, name, _image or self.image)
        with self.lock:
            if any(node.id == request.node_id for node in self._registered_nodes()):
                raise DockerConflictError("节点 ID 已存在")
            issued_credential = bool(self.credentials and _token is None)
            token = self._create_token(request.node_id, _token)
            payload["Env"] = [
                item.replace("__TASKHUB_NODE_CREDENTIAL__", token) for item in payload["Env"]
            ]
            container_id = ""
            try:
                container_id = self._create_and_start(name, payload)
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
                if container_id:
                    self.client.request("DELETE", f"/containers/{quote(container_id)}?force=true")
                if issued_credential:
                    self.credentials.revoke(request.node_id)
                raise
        if self.operation_log:
            self.operation_log.record(
                "local_docker_create", "passed", node_id=request.node_id, role=request.role
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

    def _container_payload(self, request: ContainerCreate, name: str, image: str) -> dict:
        node_command = (
            "mkdir -p /var/lib/taskhub-node/jobs; "
            "chown -R taskhub:taskhub /var/lib/taskhub-node; "
            "exec runuser -u taskhub -- python -m uvicorn "
            "taskhub_v2.node_agent:create_node_app --factory --host 0.0.0.0 --port 8020"
        )
        runtime_environment, runtime_host_config = node_runtime_configuration(
            request.role,
            self.data_volume_name,
            self.model_accounts_volume_subpath,
            self.openai_proxy_url,
            self.test_database_admin_dsn,
        )
        return {
            "Image": image,
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
                "TASKHUB_NODE_CACHE_ROOT=/var/lib/taskhub-node/cache",
                *runtime_environment,
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
                **runtime_host_config,
            },
        }

    def _create_token(self, node_id: str, token: str | None) -> str:
        try:
            return token or (
                self.credentials.issue(node_id) if self.credentials else self.node_token
            )
        except NodeCredentialError as exc:
            raise DockerUnavailableError(str(exc)) from exc

    def _create_and_start(self, name: str, payload: dict) -> str:
        status, data = self.client.request(
            "POST", f"/containers/create?name={quote(name)}", payload
        )
        if status == 409:
            raise DockerConflictError(data.get("message", "同名容器已存在"))
        if status != 201:
            raise DockerUnavailableError(data.get("message", "容器创建失败"))
        container_id = data["Id"]
        status, start_data = self.client.request("POST", f"/containers/{quote(container_id)}/start")
        if status not in {204, 304}:
            self.client.request("DELETE", f"/containers/{quote(container_id)}?force=true")
            raise DockerUnavailableError(start_data.get("message", "容器启动失败"))
        return container_id

    def rotate_credential(self, node_id: str) -> dict:
        if not self.credentials:
            raise DockerUnavailableError("当前节点仍使用旧版部署级令牌，无法独立轮换")
        with self.lock:
            container = self._find(node_id)
            node = self._registered_node(node_id)
            role = (container.get("Labels") or {}).get("io.taskhub.role", "")
            try:
                token = self.credentials.rotate(node_id)
            except NodeCredentialError as exc:
                raise DockerUnavailableError(str(exc)) from exc
            self._replace_container(container, node_id)
            result = self.create(
                ContainerCreate(node_id=node_id, role=role, slots=node.slots),
                _token=token,
            )
        return {**result, "credential": self.credentials.metadata(node_id)}

    def upgrade(self, node_id: str) -> dict:
        self._require_available()
        with self.lock:
            container = self._find(node_id)
            current_image = str(container.get("Image") or "")
            target_image = str(self.image or "")
            if not target_image:
                raise DockerUnavailableError("目标工作节点镜像尚未配置")
            if current_image == target_image:
                return {
                    "node_id": node_id,
                    "upgraded": False,
                    "image": target_image,
                    "detail": "节点已运行目标镜像",
                }
            node = self._registered_node(node_id)
            role = (container.get("Labels") or {}).get("io.taskhub.role", "")
            token = self._node_token(node_id)
            request = ContainerCreate(node_id=node_id, role=role, slots=node.slots)
            self._replace_container(container, node_id)
            try:
                created = self.create(request, _token=token, _image=target_image)
                self._wait_healthy(created["id"])
            except Exception as upgrade_error:
                self._remove_replacement(node_id)
                try:
                    restored = self.create(request, _token=token, _image=current_image)
                    self._wait_healthy(restored["id"])
                except Exception as rollback_error:
                    self._record_upgrade(node_id, "failed", current_image, target_image)
                    raise DockerUnavailableError(
                        "节点升级和旧镜像回滚均失败；节点数据卷仍保留，请查看诊断日志"
                    ) from rollback_error
                self._record_upgrade(node_id, "rolled_back", current_image, target_image)
                raise DockerUnavailableError(
                    "节点升级健康检查失败，已恢复原镜像"
                ) from upgrade_error
            self._record_upgrade(node_id, "passed", current_image, target_image)
            return {
                **created,
                "upgraded": True,
                "previous_image": current_image,
                "image": target_image,
                "detail": "节点已升级并通过健康检查",
            }

    def _registered_node(self, node_id: str):
        node = next((item for item in self._registered_nodes() if item.id == node_id), None)
        if node is None:
            raise DockerUnavailableError("节点调度记录不存在，无法安全重建")
        return node

    def _node_token(self, node_id: str) -> str:
        if not self.credentials:
            return self.node_token
        token = self.credentials.resolve(node_id)
        if not token:
            raise DockerUnavailableError("节点凭据不可用，无法安全升级")
        return token

    def _replace_container(self, container: dict, node_id: str) -> None:
        status, data = self.client.request("DELETE", f"/containers/{container['Id']}?force=true")
        if status != 204:
            raise DockerUnavailableError(data.get("message", "旧节点容器移除失败"))
        self._unregister(node_id)

    def _remove_replacement(self, node_id: str) -> None:
        try:
            container = self._find(node_id)
        except KeyError:
            return
        self._replace_container(container, node_id)

    def _wait_healthy(self, container_id: str) -> None:
        deadline = time.monotonic() + self.node_upgrade_timeout_seconds
        while time.monotonic() < deadline:
            status, payload = self.client.request("GET", f"/containers/{container_id}/json")
            if status != 200:
                raise DockerUnavailableError(payload.get("message", "无法读取节点健康状态"))
            state = payload.get("State") or {}
            health = (state.get("Health") or {}).get("Status", "")
            if state.get("Running") and health == "healthy":
                return
            if health == "unhealthy" or state.get("Status") in {"dead", "exited"}:
                raise DockerUnavailableError("节点容器未通过健康检查")
            time.sleep(0.5)
        raise DockerUnavailableError("等待节点健康检查超时")

    def _record_upgrade(
        self, node_id: str, result: str, previous_image: str, target_image: str
    ) -> None:
        if self.operation_log:
            self.operation_log.record(
                "local_docker_upgrade",
                result,
                node_id=node_id,
                previous_image=previous_image,
                target_image=target_image,
            )
