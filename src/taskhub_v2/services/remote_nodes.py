from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from taskhub_v2.domain.remote_nodes import RemoteNodeCreate
from taskhub_v2.persistence.remote_nodes import new_remote_node_record
from taskhub_v2.services import image_distribution as image_ops
from taskhub_v2.services import remote_node_helpers as node_helpers
from taskhub_v2.services.containers import (
    ContainerManager,
    DockerConflictError,
    DockerUnavailableError,
)
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
        image_registry: str = "",
        image_proxy: str = "",
        registry_username: str = "",
        registry_password: str = "",
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
        self.image_registry = image_registry.strip().strip("/")
        self.image_proxy = image_proxy.strip().strip("/")
        self.registry_username = registry_username
        self.registry_password = registry_password
        self.default_cpu = default_cpu
        self.default_memory = default_memory
        self._tasks: dict[str, asyncio.Task] = {}

    async def list(self) -> dict[str, Any]:
        return {
            "nodes": [node_helpers.remote_node_view(item) for item in await self.store.list()]
        }

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
        record = await self.store.save(
            new_remote_node_record(
                node_id=request.node_id,
                host_id=request.host_id,
                payload={
                    **request.model_dump(mode="json"),
                    "image": self.image,
                    "cpu_limit": cpu,
                    "memory_limit": memory,
                    "distribution": {
                        "phase": "queued",
                        "percent": 0,
                        "source": "",
                        "detail": "等待开始镜像分发",
                    },
                },
                container_id="",
                image_digest="",
                desired_state="running",
                actual_state="distributing",
                status_reason="等待开始镜像分发",
            )
        )
        task = asyncio.create_task(self._provision(request, host, cpu, memory))
        self._tasks[request.node_id] = task
        task.add_done_callback(
            lambda _task, node_id=request.node_id: self._tasks.pop(node_id, None)
        )
        return node_helpers.remote_node_view(record)

    async def _provision(self, request, host, cpu: str, memory: str) -> None:
        try:
            await self._progress(request.node_id, "checking", 5, "检查目标主机镜像与架构")
            await self._progress(
                request.node_id,
                "pulling",
                15,
                "正在依次尝试私有仓库、镜像代理和镜像原始地址",
            )
            pull_output = await self.hosts.run_remote_script(
                request.host_id,
                image_ops.apply_host_docker_access(
                    image_ops.pull_script(
                        self.image,
                        self.image_registry,
                        self.image_proxy,
                        self.registry_username,
                        self.registry_password,
                    ),
                    host.payload["docker_access"],
                ),
                timeout=1800,
            )
            image_values = image_ops.values(pull_output)
            source = image_values.get("SOURCE", "")
            if image_values.get("FOUND") == "1":
                await self._progress(
                    request.node_id,
                    "verifying",
                    65,
                    f"已从{image_ops.source_label(source)}取得镜像，正在核对摘要与架构",
                    source=source,
                )
                digest = image_ops.validate_remote_image(self.image, host.facts, image_values)
            else:
                detail = image_values.get("ERROR", "仓库和代理均未提供该镜像")
                await self._progress(
                    request.node_id,
                    "exporting",
                    35,
                    f"远程拉取未成功，准备从 Seed 传输：{image_ops.safe_detail(detail)}",
                    source="ssh-transfer",
                )
                digest = await self._transfer_image(request, host)
                source = "ssh-transfer"

            await self._progress(
                request.node_id,
                "creating",
                80,
                "镜像校验通过，正在创建并启动容器",
                source=source,
                digest=digest,
            )
            output = await self.hosts.run_remote_script(
                request.host_id,
                image_ops.apply_host_docker_access(
                    image_ops.create_script(
                        request, self.image, self.node_token, cpu, memory
                    ),
                    host.payload["docker_access"],
                ),
                timeout=180,
            )
            values = image_ops.values(output)
            container_id = values.get("CONTAINER", "")
            if not container_id:
                raise RemoteNodeError("远程 Docker 未返回容器 ID")
            await self._save_runtime(
                request.node_id,
                container_id=container_id,
                image_digest=digest,
                actual_state="starting",
                phase="health-check",
                percent=90,
                detail="容器已启动，正在等待 Node Agent 健康检查",
                source=source,
            )
            await self._wait_healthy(host.payload["address"], request)
            self.registry.register_node(
                node_helpers.node_definition(host.payload["address"], request)
            )
            await self._save_runtime(
                request.node_id,
                container_id=container_id,
                image_digest=digest,
                actual_state="running",
                phase="complete",
                percent=100,
                detail="镜像分发完成，Node Agent 健康且已加入调度",
                source=source,
            )
            await self._audit("create", request.node_id, request.host_id, "passed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = image_ops.safe_detail(str(exc))
            await self._save_runtime(
                request.node_id,
                actual_state="error",
                phase="failed",
                percent=100,
                detail=detail,
            )
            await self._audit("create", request.node_id, request.host_id, "failed")

    async def _transfer_image(self, request: RemoteNodeCreate, host) -> str:
        with tempfile.NamedTemporaryFile(prefix="taskhub-node-", suffix=".tar") as archive:
            try:
                local = await asyncio.to_thread(self.registry.export_image, self.image, archive)
            except DockerUnavailableError as exc:
                raise RemoteNodeError(
                    f"远程仓库拉取失败，且 Seed 本机无法导出镜像：{exc}"
                ) from exc
            archive.flush()
            image_ops.validate_architecture(
                host.facts, local.get("architecture", ""), local.get("os", "")
            )
            size = archive.tell()
            await self._progress(
                request.node_id,
                "transferring",
                50,
                f"正在通过 SSH 传输镜像（{image_ops.format_bytes(size)}）",
                source="ssh-transfer",
            )
            await self.hosts.load_remote_image(request.host_id, Path(archive.name))
        await self._progress(
            request.node_id,
            "verifying",
            70,
            "SSH 传输完成，正在核对远端镜像",
            source="ssh-transfer",
        )
        output = await self.hosts.run_remote_script(
            request.host_id,
            image_ops.apply_host_docker_access(
                image_ops.inspect_script(self.image), host.payload["docker_access"]
            ),
            timeout=60,
        )
        values = image_ops.values(output)
        digest = image_ops.validate_remote_image(self.image, host.facts, values)
        if local.get("id") and values.get("IMAGE_ID") != local["id"]:
            raise RemoteNodeError("SSH 传输后的镜像摘要与 Seed 本机镜像不一致")
        return digest

    async def _progress(
        self,
        node_id: str,
        phase: str,
        percent: int,
        detail: str,
        *,
        source: str = "",
        digest: str = "",
    ) -> None:
        await self._save_runtime(
            node_id,
            actual_state="distributing",
            phase=phase,
            percent=percent,
            detail=detail,
            source=source,
            digest=digest,
            image_digest=digest or None,
        )

    async def _save_runtime(
        self,
        node_id: str,
        *,
        actual_state: str,
        phase: str,
        percent: int,
        detail: str,
        source: str = "",
        digest: str = "",
        image_digest: str | None = None,
        container_id: str | None = None,
    ):
        record = await self.store.get(node_id)
        if not record:
            return None
        previous = dict(record.payload.get("distribution") or {})
        payload = {
            **record.payload,
            "distribution": {
                "phase": phase,
                "percent": percent,
                "source": source or previous.get("source", ""),
                "detail": detail,
                "digest": digest or previous.get("digest", ""),
            },
        }
        return await self.store.save(
            replace(
                record,
                payload=payload,
                container_id=record.container_id if container_id is None else container_id,
                image_digest=(
                    record.image_digest if image_digest is None else image_digest
                ),
                actual_state=actual_state,
                status_reason=detail,
            )
        )

    async def action(self, node_id: str, action: str, *, remove_volume: bool = False) -> dict:
        record = await self.store.get(node_id)
        if not record:
            raise KeyError(node_id)
        host = await self.host_store.get(record.host_id)
        if not host:
            raise RemoteNodeError("节点所属物理主机不存在")
        if not record.container_id:
            if action != "remove":
                raise RemoteNodeError("镜像分发尚未完成，当前只能移除该节点记录")
            task = self._tasks.get(node_id)
            if task and not task.done():
                task.cancel()
            await self.store.delete(node_id)
            await self._audit("remove", node_id, record.host_id, "passed")
            return {"node_id": node_id, "action": action, "ok": True}
        output = await self.hosts.run_remote_script(
            record.host_id,
            image_ops.action_script(
                node_id, host.payload["docker_access"], action, remove_volume
            ),
            timeout=120,
        )
        if action == "remove":
            self.registry.unregister_node(node_id)
            await self.store.delete(node_id)
            await self._audit("remove", node_id, record.host_id, "passed")
            return {"node_id": node_id, "action": action, "ok": True}
        values = image_ops.values(output)
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
            self.registry.register_node(
                node_helpers.node_definition(host.payload["address"], request)
            )
            reason = "Node Agent 健康且已加入调度"
            state = "running"
        saved = await self.store.save(
            replace(record, desired_state=desired, actual_state=state, status_reason=reason)
        )
        await self._audit(action, node_id, record.host_id, "passed")
        return node_helpers.remote_node_view(saved)

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_healthy(self, address: str, request: RemoteNodeCreate) -> None:
        try:
            await node_helpers.wait_healthy(address, request, self.node_token)
        except RuntimeError as exc:
            raise RemoteNodeError(str(exc)) from exc

    async def _audit(self, action: str, node_id: str, host_id: str, result: str) -> None:
        await self.configuration_store.add_audit(
            operator="admin",
            scope="physical_hosts",
            action=action,
            parameter_summary={"node_id": node_id, "host_id": host_id},
            result=result,
        )
