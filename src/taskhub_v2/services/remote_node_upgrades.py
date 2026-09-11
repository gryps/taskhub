from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from uuid import uuid4

from taskhub_v2.services import image_distribution as image_ops
from taskhub_v2.services import remote_node_helpers as node_helpers
from taskhub_v2.services.remote_node_errors import RemoteNodeError


class RemoteNodeUpgradeMixin:
    async def upgrade(self, node_id: str, image: str) -> dict:
        image = image.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:/@-]+", image):
            raise RemoteNodeError("目标镜像格式无效")
        record = await self.store.get(node_id)
        if not record:
            raise KeyError(node_id)
        if node_id in self._tasks:
            raise RemoteNodeError("该节点已有后台操作正在执行")
        if record.desired_state != "running":
            raise RemoteNodeError("请先启动节点，再升级镜像")
        current_image = record.payload.get("image") or self.image
        if image == current_image:
            raise RemoteNodeError("目标镜像与当前镜像相同")
        host = await self.host_store.get(record.host_id)
        if not host or host.status != "available":
            raise RemoteNodeError("节点所属主机当前不可用")
        operation = {
            "id": self._new_operation_id(), "kind": "upgrade", "attempt": 1,
            "status": "queued", "phase": "queued", "percent": 0,
            "target_image": image, "previous_image": current_image,
            "previous_digest": record.image_digest, "detail": "等待开始镜像升级",
        }
        saved = await self.store.save(replace(
            record,
            payload={
                **record.payload, "operation": operation,
                "distribution": {"phase": "queued", "percent": 0, "source": "",
                                 "detail": "等待开始镜像升级", "digest": ""},
            },
            actual_state="upgrading", status_reason="等待开始镜像升级",
        ))
        self._schedule_node_task(node_id, self._upgrade_operation(node_id))
        return node_helpers.remote_node_view(saved, self._credential_metadata(node_id))

    async def resume_pending_operations(self) -> None:
        for record in await self.store.list():
            operation = dict(record.payload.get("operation") or {})
            pending = operation.get("status") in {"queued", "running"}
            legacy_create = record.actual_state in {"distributing", "starting"}
            if record.node_id in self._tasks or not (pending or legacy_create):
                continue
            await self._increment_operation_attempt(record)
            if operation.get("kind") == "upgrade":
                work = self._resume_upgrade(record.node_id)
            else:
                work = self._resume_create(record.node_id)
            self._schedule_node_task(record.node_id, work)

    @staticmethod
    def _new_operation_id() -> str:
        return uuid4().hex

    async def _increment_operation_attempt(self, record) -> None:
        operation = dict(record.payload.get("operation") or {})
        operation.setdefault("id", self._new_operation_id())
        operation.setdefault("kind", "create")
        operation["attempt"] = int(operation.get("attempt", 0)) + 1
        await self.store.save(replace(
            record, payload={**record.payload, "operation": operation}
        ))

    def _schedule_node_task(self, node_id: str, work) -> None:
        task = asyncio.create_task(work)
        self._tasks[node_id] = task
        task.add_done_callback(
            lambda _task, current=node_id: self._tasks.pop(current, None)
        )

    async def _resume_create(self, node_id: str) -> None:
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            record = await self.store.get(node_id)
            if not record:
                return
            host = await self.host_store.get(record.host_id)
            if not host:
                await self._mark_operation_failed(record, "节点所属物理主机不存在")
                return
            request = self._request(record)
            token = self._ensure_credential(node_id)
            try:
                output = await self.hosts.run_remote_script(
                    record.host_id,
                    image_ops.runtime_script(node_id, host.payload["docker_access"]),
                    timeout=45,
                )
                runtime = image_ops.values(output)
                if runtime.get("EXISTS") == "1":
                    if runtime.get("STATE") != "running":
                        await self.hosts.run_remote_script(
                            record.host_id,
                            image_ops.action_script(
                                node_id, host.payload["docker_access"], "start", False
                            ),
                            timeout=60,
                        )
                    await self._wait_healthy(host.payload["address"], request, token)
                    self.registry.register_node(
                        node_helpers.node_definition(host.payload["address"], request)
                    )
                    await self._save_runtime(
                        node_id, container_id=runtime.get("CONTAINER", ""),
                        image_digest=record.image_digest, actual_state="running",
                        phase="complete", percent=100,
                        detail="Seed 重启后已恢复节点创建并加入调度",
                    )
                    return
                await self._provision_locked(
                    request, host, record.payload.get("cpu_limit", ""),
                    record.payload.get("memory_limit", ""), token,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._mark_operation_failed(record, image_ops.safe_detail(str(exc)))

    async def _upgrade_operation(self, node_id: str) -> None:
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            await self._upgrade_locked(node_id)

    async def _upgrade_locked(self, node_id: str) -> None:
        record = await self.store.get(node_id)
        if not record:
            return
        operation = dict(record.payload.get("operation") or {})
        target = operation.get("target_image", "")
        previous = operation.get("previous_image") or record.payload.get("image") or self.image
        host = await self.host_store.get(record.host_id)
        if not host:
            await self._mark_operation_failed(record, "节点所属物理主机不存在")
            return
        request = self._request(record)
        token = self._resolve_credential(node_id) or self._ensure_credential(node_id)
        replaced = False
        try:
            digest, source = await self._acquire_upgrade_image(record, host, target)
            await self._progress(
                node_id, "replacing", 80, "镜像校验通过，正在替换节点容器",
                source=source, digest=digest,
            )
            # Once replacement starts, a lost SSH response cannot prove that the
            # old container survived. Always take the rollback path on uncertainty.
            replaced = True
            output = await self.hosts.run_remote_script(
                record.host_id,
                image_ops.apply_host_docker_access(
                    image_ops.create_script(
                        request, target, token, record.payload.get("cpu_limit", ""),
                        record.payload.get("memory_limit", ""), replace_existing=True,
                    ),
                    host.payload["docker_access"],
                ),
                timeout=180,
            )
            await self._progress(
                node_id, "health-check", 90,
                "新容器已启动，正在验证 Node Agent 身份和健康状态",
                source=source, digest=digest,
            )
            await self._wait_healthy(host.payload["address"], request, token)
            self.registry.register_node(
                node_helpers.node_definition(host.payload["address"], request)
            )
            await self._finish_upgrade(
                node_id, target, digest, image_ops.values(output).get("CONTAINER", ""),
                "complete", "节点镜像升级完成，健康检查通过并已恢复调度",
            )
            await self._audit("upgrade", node_id, record.host_id, "passed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = image_ops.safe_detail(str(exc))
            if replaced:
                await self._rollback_upgrade(record, host, request, token, previous, detail)
            else:
                await self._mark_operation_failed(
                    record, f"升级失败，原容器继续运行：{detail}", state="running"
                )
            await self._audit("upgrade", node_id, record.host_id, "failed")

    async def _acquire_upgrade_image(self, record, host, image: str) -> tuple[str, str]:
        await self._progress(
            record.node_id, "pulling", 15,
            "正在从私有仓库、镜像代理和镜像原始地址获取升级镜像",
        )
        output = await self.hosts.run_remote_script(
            record.host_id,
            image_ops.apply_host_docker_access(
                image_ops.pull_script(
                    image, self.image_registry, self.image_proxy,
                    self.registry_username, self.registry_password,
                ),
                host.payload["docker_access"],
            ),
            timeout=1800,
        )
        values = image_ops.values(output)
        source = values.get("SOURCE", "")
        if values.get("FOUND") == "1":
            await self._progress(
                record.node_id, "verifying", 65,
                f"已从{image_ops.source_label(source)}取得升级镜像，正在校验",
                source=source,
            )
            return image_ops.validate_remote_image(image, host.facts, values), source
        await self._progress(
            record.node_id, "exporting", 35,
            "远程拉取未成功，准备由 Seed 通过 SSH 传输升级镜像",
            source="ssh-transfer",
        )
        return await self._transfer_image(self._request(record), host, image), "ssh-transfer"

    async def _resume_upgrade(self, node_id: str) -> None:
        record = await self.store.get(node_id)
        if not record:
            return
        operation = dict(record.payload.get("operation") or {})
        host = await self.host_store.get(record.host_id)
        if not host:
            await self._mark_operation_failed(record, "节点所属物理主机不存在")
            return
        output = await self.hosts.run_remote_script(
            record.host_id,
            image_ops.runtime_script(node_id, host.payload["docker_access"]),
            timeout=45,
        )
        runtime = image_ops.values(output)
        target = operation.get("target_image", "")
        if runtime.get("EXISTS") == "1" and runtime.get("IMAGE") == target:
            request = self._request(record)
            token = self._resolve_credential(node_id)
            try:
                await self._wait_healthy(host.payload["address"], request, token)
                self.registry.register_node(
                    node_helpers.node_definition(host.payload["address"], request)
                )
                await self._finish_upgrade(
                    node_id, target, record.image_digest,
                    runtime.get("CONTAINER", record.container_id), "complete",
                    "Seed 重启后确认升级容器健康，已恢复调度",
                )
                return
            except Exception as exc:
                await self._rollback_upgrade(
                    record, host, request, token,
                    operation.get("previous_image") or self.image,
                    image_ops.safe_detail(str(exc)),
                )
                return
        await self._upgrade_operation(node_id)

    async def _rollback_upgrade(
        self, record, host, request, token: str, previous: str, failure: str
    ) -> None:
        self.registry.unregister_node(record.node_id)
        await self._progress(
            record.node_id, "rolling-back", 95,
            f"新版本健康检查失败，正在恢复原镜像：{failure}",
        )
        try:
            output = await self.hosts.run_remote_script(
                record.host_id,
                image_ops.apply_host_docker_access(
                    image_ops.create_script(
                        request, previous, token, record.payload.get("cpu_limit", ""),
                        record.payload.get("memory_limit", ""), replace_existing=True,
                    ),
                    host.payload["docker_access"],
                ),
                timeout=180,
            )
            await self._wait_healthy(host.payload["address"], request, token)
            self.registry.register_node(
                node_helpers.node_definition(host.payload["address"], request)
            )
            operation = record.payload.get("operation") or {}
            await self._finish_upgrade(
                record.node_id, previous, operation.get("previous_digest", ""),
                image_ops.values(output).get("CONTAINER", ""), "rollback-complete",
                f"升级失败，已自动恢复原镜像：{failure}",
            )
        except Exception as rollback_error:
            await self._mark_operation_failed(
                record,
                f"升级失败且自动回滚失败：{failure}；{image_ops.safe_detail(str(rollback_error))}",
                phase="rollback-failed", state="offline",
            )

    async def _finish_upgrade(
        self, node_id: str, image: str, digest: str, container_id: str,
        phase: str, detail: str,
    ) -> None:
        await self._save_runtime(
            node_id, container_id=container_id, image_digest=digest,
            actual_state="running", phase=phase, percent=100, detail=detail,
            digest=digest,
        )
        current = await self.store.get(node_id)
        if current:
            await self.store.save(replace(
                current, payload={**current.payload, "image": image}
            ))

    async def _mark_operation_failed(
        self, record, detail: str, *, phase: str = "failed", state: str = "error"
    ) -> None:
        await self._save_runtime(
            record.node_id, actual_state=state, phase=phase, percent=100,
            detail=detail,
        )
