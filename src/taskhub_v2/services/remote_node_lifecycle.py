from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from taskhub_v2.domain.remote_nodes import RemoteNodeCreate
from taskhub_v2.security.node_credentials import NodeCredentialError
from taskhub_v2.services import image_distribution as image_ops
from taskhub_v2.services import remote_node_helpers as node_helpers
from taskhub_v2.services.remote_node_errors import RemoteNodeError


class RemoteNodeLifecycleMixin:
    async def action(self, node_id: str, action: str, *, remove_volume: bool = False) -> dict:
        task = self._tasks.get(node_id)
        if action == "remove" and task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            return await self._action_locked(node_id, action, remove_volume=remove_volume)

    async def _action_locked(
        self, node_id: str, action: str, *, remove_volume: bool = False
    ) -> dict:
        record = await self.store.get(node_id)
        if not record:
            raise KeyError(node_id)
        host = await self.host_store.get(record.host_id)
        if not host:
            if action == "remove":
                self.registry.unregister_node(node_id)
                self._revoke_credential(node_id)
                await self.store.delete(node_id)
                await self._audit("remove", node_id, record.host_id, "failed")
                return {
                    "node_id": node_id,
                    "action": action,
                    "ok": True,
                    "warning": "节点记录与凭据已删除，但所属主机缺失，无法确认远程容器",
                }
            raise RemoteNodeError("节点所属物理主机不存在")
        if not record.container_id:
            if action != "remove":
                raise RemoteNodeError("镜像分发尚未完成，当前只能移除该节点记录")
            await self.hosts.run_remote_script(
                record.host_id,
                image_ops.action_script(
                    node_id, host.payload["docker_access"], "remove", remove_volume
                ),
                timeout=120,
            )
            self._revoke_credential(node_id)
            await self.store.delete(node_id)
            await self._audit("remove", node_id, record.host_id, "passed")
            return {"node_id": node_id, "action": action, "ok": True}
        token = self._resolve_credential(node_id)
        if action in {"start", "restart"} and not token:
            token = self._ensure_credential(node_id)
            output = await self.hosts.run_remote_script(
                record.host_id,
                image_ops.apply_host_docker_access(
                    image_ops.create_script(
                        self._request(record),
                        record.payload.get("image") or self.image,
                        token,
                        record.payload.get("cpu_limit", ""),
                        record.payload.get("memory_limit", ""),
                        replace_existing=True,
                    ),
                    host.payload["docker_access"],
                ),
                timeout=180,
            )
        else:
            output = await self.hosts.run_remote_script(
                record.host_id,
                image_ops.action_script(
                    node_id, host.payload["docker_access"], action, remove_volume
                ),
                timeout=120,
            )
        if action == "remove":
            self.registry.unregister_node(node_id)
            self._revoke_credential(node_id)
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
            request = self._request(record)
            await self._wait_healthy(
                host.payload["address"], request, token
            )
            self.registry.register_node(
                node_helpers.node_definition(host.payload["address"], request)
            )
            reason = "Node Agent 健康且已加入调度"
            state = "running"
        saved = await self.store.save(
            replace(
                record,
                container_id=values.get("CONTAINER", record.container_id),
                desired_state=desired,
                actual_state=state,
                status_reason=reason,
            )
        )
        await self._audit(action, node_id, record.host_id, "passed")
        return node_helpers.remote_node_view(saved, self._credential_metadata(node_id))

    async def revoke_credential(self, node_id: str) -> dict:
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            record = await self.store.get(node_id)
            if not record:
                raise KeyError(node_id)
            host = await self.host_store.get(record.host_id)
            if not self.credentials:
                raise RemoteNodeError("当前节点仍使用旧版部署级令牌，无法独立吊销")
            stopped = bool(host)
            detail = (
                "节点凭据已吊销，容器已停止并退出调度"
                if host
                else "节点凭据已吊销并退出调度；所属物理主机记录不存在"
            )
            try:
                if host:
                    await self.hosts.run_remote_script(
                        record.host_id,
                        image_ops.action_script(
                            node_id, host.payload["docker_access"], "stop", False
                        ),
                        timeout=60,
                    )
            except Exception as exc:
                stopped = False
                detail = (
                    "节点凭据已吊销并退出调度；远程容器停止失败："
                    f"{image_ops.safe_detail(str(exc))}"
                )
            self._revoke_credential(node_id)
            self.registry.unregister_node(node_id)
            saved = await self.store.save(
                replace(
                    record,
                    desired_state="stopped",
                    actual_state="stopped" if stopped else "offline",
                    status_reason=detail,
                )
            )
            await self._audit(
                "revoke_credential", node_id, record.host_id,
                "passed" if stopped else "failed",
            )
            return node_helpers.remote_node_view(
                saved, self._credential_metadata(node_id)
            )

    async def rotate_credential(self, node_id: str) -> dict:
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            return await self._rotate_credential_locked(node_id)

    async def _rotate_credential_locked(self, node_id: str) -> dict:
        record = await self.store.get(node_id)
        if not record:
            raise KeyError(node_id)
        host = await self.host_store.get(record.host_id)
        if not host:
            raise RemoteNodeError("节点所属物理主机不存在")
        if not self.credentials:
            raise RemoteNodeError("当前节点仍使用旧版部署级令牌，无法独立轮换")
        if record.desired_state != "running":
            raise RemoteNodeError("请先启动节点，再轮换独立凭据")
        request = self._request(record)
        try:
            token = self.credentials.rotate(node_id)
        except NodeCredentialError as exc:
            raise RemoteNodeError(str(exc)) from exc
        try:
            output = await self.hosts.run_remote_script(
                record.host_id,
                image_ops.apply_host_docker_access(
                    image_ops.create_script(
                        request,
                        record.payload.get("image") or self.image,
                        token,
                        record.payload.get("cpu_limit", ""),
                        record.payload.get("memory_limit", ""),
                        replace_existing=True,
                    ),
                    host.payload["docker_access"],
                ),
                timeout=180,
            )
            values = image_ops.values(output)
            await self._wait_healthy(host.payload["address"], request, token)
            self.registry.register_node(
                node_helpers.node_definition(host.payload["address"], request)
            )
            saved = await self.store.save(
                replace(
                    record,
                    container_id=values.get("CONTAINER", record.container_id),
                    desired_state="running",
                    actual_state="running",
                    status_reason="节点独立凭据已轮换，Agent 已重新加入调度",
                )
            )
        except Exception as exc:
            self.registry.unregister_node(node_id)
            await self.store.save(
                replace(
                    record,
                    actual_state="offline",
                    status_reason=f"凭据轮换后节点尚未恢复：{image_ops.safe_detail(str(exc))}",
                )
            )
            await self._audit("rotate_credential", node_id, record.host_id, "failed")
            raise RemoteNodeError("凭据已轮换，但节点重建或健康检查失败") from exc
        await self._audit("rotate_credential", node_id, record.host_id, "passed")
        return node_helpers.remote_node_view(saved, self._credential_metadata(node_id))

    def start_reconciliation(self) -> None:
        if not self._reconcile_task or self._reconcile_task.done():
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def reconcile_once(self) -> None:
        records = await self.store.list()
        active = [item for item in records if item.node_id not in self._tasks]
        if active:
            await asyncio.gather(
                *(self.hosts.check(host_id) for host_id in {item.host_id for item in active}),
                return_exceptions=True,
            )
            await asyncio.gather(
                *(self._reconcile_node(item.node_id) for item in active),
                return_exceptions=True,
            )

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.reconcile_interval_seconds)

    async def _reconcile_node(self, node_id: str) -> None:
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            record = await self.store.get(node_id)
            if not record:
                return
            host = await self.host_store.get(record.host_id)
            if not host:
                await self._mark_offline(record, "所属物理主机记录不存在")
                return
            try:
                if host.status != "available":
                    raise RemoteNodeError(f"物理主机不可用：{host.status_reason}")
                output = await self.hosts.run_remote_script(
                    record.host_id,
                    image_ops.runtime_script(node_id, host.payload["docker_access"]),
                    timeout=45,
                )
                runtime = image_ops.values(output)
                request = self._request(record)
                token = self._resolve_credential(node_id)
                credential_created = False
                if not token:
                    token = self._ensure_credential(node_id)
                    credential_created = True
                if record.desired_state == "stopped":
                    if runtime.get("EXISTS") == "1" and runtime.get("STATE") == "running":
                        await self.hosts.run_remote_script(
                            record.host_id,
                            image_ops.action_script(
                                node_id, host.payload["docker_access"], "stop", False
                            ),
                            timeout=60,
                        )
                    self.registry.unregister_node(node_id)
                    await self._save_reconciled(record, "stopped", "期望停止，已退出调度")
                    return
                if runtime.get("EXISTS") != "1" or credential_created:
                    output = await self.hosts.run_remote_script(
                        record.host_id,
                        image_ops.apply_host_docker_access(
                            image_ops.create_script(
                                request,
                                record.payload.get("image") or self.image,
                                token,
                                record.payload.get("cpu_limit", ""),
                                record.payload.get("memory_limit", ""),
                                replace_existing=credential_created,
                            ),
                            host.payload["docker_access"],
                        ),
                        timeout=180,
                    )
                    runtime = {"STATE": "running", "EXISTS": "1", **image_ops.values(output)}
                elif runtime.get("STATE") != "running":
                    output = await self.hosts.run_remote_script(
                        record.host_id,
                        image_ops.action_script(
                            node_id, host.payload["docker_access"], "start", False
                        ),
                        timeout=60,
                    )
                    runtime.update(image_ops.values(output))
                await self._wait_healthy(host.payload["address"], request, token)
                self.registry.register_node(
                    node_helpers.node_definition(host.payload["address"], request)
                )
                await self._save_reconciled(
                    record,
                    "running",
                    "期望状态、远程容器与 Node Agent 已一致",
                    container_id=runtime.get("CONTAINER") or record.container_id,
                    seen=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._mark_offline(record, image_ops.safe_detail(str(exc)))

    async def _save_reconciled(
        self, record, state: str, detail: str, *, container_id: str | None = None,
        seen: bool = False,
    ) -> None:
        current = await self.store.get(record.node_id)
        if not current:
            return
        reconciliation = dict(current.payload.get("reconciliation") or {})
        reconciliation.update({"checked_at": datetime.now(UTC).isoformat(), "detail": detail})
        if seen:
            reconciliation["agent_last_seen_at"] = datetime.now(UTC).isoformat()
        await self.store.save(
            replace(
                current,
                payload={**current.payload, "reconciliation": reconciliation},
                container_id=container_id or current.container_id,
                actual_state=state,
                status_reason=detail,
            )
        )

    async def _mark_offline(self, record, detail: str) -> None:
        self.registry.unregister_node(record.node_id)
        await self._save_reconciled(record, "offline", f"自动协调：{detail}")

    @staticmethod
    def _request(record) -> RemoteNodeCreate:
        return RemoteNodeCreate.model_validate(
            {key: record.payload[key] for key in RemoteNodeCreate.model_fields}
        )

    async def close(self) -> None:
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._reconcile_task:
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
