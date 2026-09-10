from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import replace
from typing import Any

import httpx

from taskhub_v2.domain.models import NodeDefinition
from taskhub_v2.domain.remote_nodes import RemoteNodeCreate
from taskhub_v2.persistence.remote_nodes import new_remote_node_record
from taskhub_v2.services.containers import ROLE_WORKLOADS, ContainerManager, DockerConflictError
from taskhub_v2.services.hosts import PhysicalHostService


class RemoteNodeError(RuntimeError):
    pass


class RemoteNodeService:
    def __init__(
        self,
        store,
        hosts: PhysicalHostService,
        host_store,
        configuration_store,
        registry: ContainerManager,
        *,
        image: str,
        node_token: str,
        default_cpu: str = "",
        default_memory: str = "",
    ):
        self.store = store
        self.hosts = hosts
        self.host_store = host_store
        self.configuration_store = configuration_store
        self.registry = registry
        self.image = image
        self.node_token = node_token
        self.default_cpu = default_cpu
        self.default_memory = default_memory

    async def list(self) -> dict[str, Any]:
        return {"nodes": [_view(item) for item in await self.store.list()]}

    async def create(self, request: RemoteNodeCreate) -> dict[str, Any]:
        if not self.node_token:
            raise RemoteNodeError("尚未配置节点通信令牌")
        if not re.fullmatch(r"[A-Za-z0-9._:/@-]+", self.image or ""):
            raise RemoteNodeError("默认工作节点镜像未配置或格式无效")
        host = await self.host_store.get(request.host_id)
        if not host:
            raise KeyError(request.host_id)
        allowed_roles = host.payload.get("allowed_roles", ["execution", "test", "preproduction"])
        if request.role not in allowed_roles:
            raise RemoteNodeError(f"主机 {request.host_id} 不允许承载该节点角色")
        if await self.store.get(request.node_id) or self.registry.node_exists(request.node_id):
            raise DockerConflictError("节点 ID 已存在")

        cpu = request.cpu_limit or self.default_cpu
        memory = request.memory_limit or self.default_memory
        script = apply_host_docker_access(
            _create_script(request, self.image, self.node_token, cpu, memory),
            host.payload["docker_access"],
        )
        output = await self.hosts.run_remote_script(
            request.host_id,
            script,
            timeout=300,
        )
        values = _values(output)
        container_id = values.get("CONTAINER", "")
        if not container_id:
            raise RemoteNodeError("远程 Docker 未返回容器 ID")
        record = new_remote_node_record(
            node_id=request.node_id,
            host_id=request.host_id,
            payload={
                **request.model_dump(mode="json"),
                "image": self.image,
                "cpu_limit": cpu,
                "memory_limit": memory,
            },
            container_id=container_id,
            image_digest=values.get("IMAGE", ""),
            desired_state="running",
            actual_state=values.get("STATE", "running"),
            status_reason="容器已创建，正在等待 Node Agent 健康检查",
        )
        saved = await self.store.save(record)
        try:
            await self._wait_healthy(host.payload["address"], request)
        except RemoteNodeError as exc:
            saved = await self.store.save(
                replace(saved, actual_state="starting", status_reason=str(exc))
            )
            await self._audit("create", request.node_id, request.host_id, "failed")
            return _view(saved)
        self.registry.register_node(_definition(host.payload["address"], request))
        saved = await self.store.save(
            replace(saved, actual_state="running", status_reason="Node Agent 健康且已加入调度")
        )
        await self._audit("create", request.node_id, request.host_id, "passed")
        return _view(saved)

    async def action(self, node_id: str, action: str, *, remove_volume: bool = False) -> dict:
        record = await self.store.get(node_id)
        if not record:
            raise KeyError(node_id)
        host = await self.host_store.get(record.host_id)
        if not host:
            raise RemoteNodeError("节点所属物理主机不存在")
        output = await self.hosts.run_remote_script(
            record.host_id,
            _action_script(node_id, host.payload["docker_access"], action, remove_volume),
            timeout=120,
        )
        if action == "remove":
            self.registry.unregister_node(node_id)
            await self.store.delete(node_id)
            await self._audit("remove", node_id, record.host_id, "passed")
            return {"node_id": node_id, "action": action, "ok": True}
        values = _values(output)
        state = values.get("STATE", "unknown")
        desired = "stopped" if action == "stop" else "running"
        reason = f"远程容器已{ {'start': '启动', 'stop': '停止', 'restart': '重启'}[action] }"
        if action == "stop":
            self.registry.unregister_node(node_id)
        else:
            request = RemoteNodeCreate.model_validate(
                {key: record.payload[key] for key in RemoteNodeCreate.model_fields}
            )
            await self._wait_healthy(host.payload["address"], request)
            self.registry.register_node(_definition(host.payload["address"], request))
            reason = "Node Agent 健康且已加入调度"
            state = "running"
        saved = await self.store.save(
            replace(record, desired_state=desired, actual_state=state, status_reason=reason)
        )
        await self._audit(action, node_id, record.host_id, "passed")
        return _view(saved)

    async def _wait_healthy(self, address: str, request: RemoteNodeCreate) -> None:
        url = f"http://{address}:{request.host_port}/api/health"
        last_error = "Node Agent 尚未响应"
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            for _ in range(10):
                try:
                    response = await client.get(
                        url, headers={"Authorization": f"Bearer {self.node_token}"}
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("node_id") == request.node_id:
                        return
                    last_error = "Node Agent 返回了不匹配的节点 ID"
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = str(exc)[:200]
                await asyncio.sleep(2)
        raise RemoteNodeError(f"容器已启动，但健康检查未通过：{last_error}")

    async def _audit(self, action: str, node_id: str, host_id: str, result: str) -> None:
        await self.configuration_store.add_audit(
            operator="admin",
            scope="physical_hosts",
            action=action,
            parameter_summary={"node_id": node_id, "host_id": host_id},
            result=result,
        )


def _docker_prefix(access: str) -> str:
    return "sudo -n docker" if access == "sudo" else "docker"


def _create_script(
    request: RemoteNodeCreate, image: str, token: str, cpu: str, memory: str
) -> str:
    name = f"taskhub-node-{request.node_id}"
    quoted_image = shlex.quote(image)
    args = [
        "create",
        "--name",
        name,
        "--label",
        "io.taskhub.managed=true",
        "--label",
        f"io.taskhub.node-id={request.node_id}",
        "--label",
        f"io.taskhub.role={request.role}",
        "--label",
        f"io.taskhub.host-id={request.host_id}",
        "--restart",
        "unless-stopped",
        "--env-file",
        '"$env_file"',
        "-p",
        f"{request.host_port}:8020",
        "-v",
        f"{name}-data:/var/lib/taskhub-node",
    ]
    if cpu:
        args.extend(["--cpus", cpu])
    if memory:
        args.extend(["--memory", memory])
    args.append(image)
    command = " ".join(item if item == '"$env_file"' else shlex.quote(item) for item in args)
    token_line = shlex.quote(f"TASKHUB_NODE_TOKEN={token}")
    script = f"""set -eu
docker_run() {{ __TASKHUB_DOCKER__ "$@"; }}
if docker_run container inspect {shlex.quote(name)} >/dev/null 2>&1; then
  echo '节点容器已存在' >&2
  exit 17
fi
docker_run image inspect {quoted_image} >/dev/null 2>&1 || \
  docker_run pull {quoted_image}
env_file=$(mktemp)
trap 'rm -f "$env_file"' EXIT
chmod 600 "$env_file"
printf '%s\n' 'TASKHUB_NODE_ID={request.node_id}' 'TASKHUB_NODE_ROLE={request.role}' \
  'TASKHUB_NODE_WORK_ROOT=/var/lib/taskhub-node/jobs' {token_line} > "$env_file"
container=$(docker_run {command})
docker_run start "$container" >/dev/null
image_value=$(docker_run image inspect {quoted_image} \
  --format '{{{{index .RepoDigests 0}}}}' 2>/dev/null || true)
[ -n "$image_value" ] || image_value=$(docker_run image inspect {quoted_image} \
  --format '{{{{.Id}}}}')
state=$(docker_run inspect "$container" --format '{{{{.State.Status}}}}')
printf 'CONTAINER=%s\nIMAGE=%s\nSTATE=%s\n' "$container" "$image_value" "$state"
"""
    return script


def _action_script(node_id: str, docker_access: str, action: str, remove_volume: bool) -> str:
    docker = _docker_prefix(docker_access)
    name = f"taskhub-node-{node_id}"
    if action == "remove":
        volume = f"{name}-data"
        remove = f"\n{docker} volume rm {shlex.quote(volume)} >/dev/null" if remove_volume else ""
        return f"set -eu\n{docker} rm -f {shlex.quote(name)} >/dev/null{remove}\n"
    command = {"start": "start", "stop": "stop", "restart": "restart"}[action]
    return (
        f"set -eu\n{docker} {command} {shlex.quote(name)} >/dev/null\n"
        f"state=$({docker} inspect {shlex.quote(name)} --format '{{{{.State.Status}}}}')\n"
        "printf 'STATE=%s\\n' \"$state\"\n"
    )


def apply_host_docker_access(script: str, docker_access: str) -> str:
    return script.replace("__TASKHUB_DOCKER__", _docker_prefix(docker_access))


def _values(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _definition(address: str, request: RemoteNodeCreate) -> NodeDefinition:
    return NodeDefinition(
        id=request.node_id,
        kind="remote",
        url=f"http://{address}:{request.host_port}",
        slots=request.slots,
        workloads=ROLE_WORKLOADS[request.role],
    )


def _view(record) -> dict[str, Any]:
    return {
        **record.payload,
        "container_id": record.container_id,
        "image_digest": record.image_digest,
        "desired_state": record.desired_state,
        "actual_state": record.actual_state,
        "status_reason": record.status_reason,
        "updated_at": record.updated_at.isoformat(),
    }
