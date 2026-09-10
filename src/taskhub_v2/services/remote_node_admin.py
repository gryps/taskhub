from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from taskhub_v2.services import image_distribution as image_ops
from taskhub_v2.services import remote_node_helpers as node_helpers
from taskhub_v2.services.remote_node_errors import RemoteNodeError


class RemoteNodeAdminMixin:
    async def suspend_host(self, host_id: str, reason: str) -> int:
        count = 0
        for record in await self.store.list():
            if record.host_id != host_id:
                continue
            self.registry.unregister_node(record.node_id)
            payload = {**record.payload, "host_suspended": True}
            await self.store.save(
                replace(record, payload=payload, actual_state="draining", status_reason=reason)
            )
            count += 1
        return count

    async def resume_host(self, host_id: str) -> int:
        records = [item for item in await self.store.list() if item.host_id == host_id]
        for record in records:
            await self.store.save(
                replace(record, payload={**record.payload, "host_suspended": False})
            )
        await asyncio.gather(
            *(self._reconcile_node(item.node_id) for item in records), return_exceptions=True
        )
        return len(records)

    async def rebuild_host_nodes(
        self, source_host_id: str, target_host_id: str, node_ids: list[str]
    ) -> dict[str, Any]:
        target = await self.host_store.get(target_host_id)
        if not target or target.status != "available":
            raise RemoteNodeError("目标主机不存在或当前不可用")
        selected = [
            item for item in await self.store.list()
            if item.host_id == source_host_id and (not node_ids or item.node_id in node_ids)
        ]
        results = []
        for record in selected:
            results.append(
                await self._rebuild_node(record, source_host_id, target_host_id)
            )
        outcome = "passed" if all(item["ok"] for item in results) else "failed"
        await self._audit(
            "batch_rebuild", ",".join(item.node_id for item in selected),
            target_host_id, outcome,
        )
        return {"source_host_id": source_host_id, "target_host_id": target_host_id,
                "results": results}

    async def _rebuild_node(self, record, source_host_id: str, target_host_id: str) -> dict:
        try:
            source = await self.host_store.get(source_host_id)
            if source:
                await self.hosts.run_remote_script(
                    source_host_id,
                    image_ops.action_script(
                        record.node_id, source.payload["docker_access"], "remove", False
                    ),
                    timeout=120, allow_maintenance=True,
                    operation="node_rebuild_remove", node_id=record.node_id,
                )
            payload = {
                **record.payload, "host_id": target_host_id, "host_suspended": False,
                "migration": {"source_host_id": source_host_id,
                              "target_host_id": target_host_id,
                              "detail": "源主机数据卷保留；目标主机按期望状态重建"},
            }
            moved = await self.store.save(
                replace(record, host_id=target_host_id, payload=payload, container_id="",
                        actual_state="offline", status_reason="等待在目标主机重建")
            )
            await self._reconcile_node(moved.node_id)
            current = await self.store.get(moved.node_id)
            ok = bool(current and current.actual_state == "running")
            return {"node_id": moved.node_id, "ok": ok,
                    "detail": current.status_reason if current else "节点记录丢失"}
        except Exception as exc:
            return {"node_id": record.node_id, "ok": False,
                    "detail": image_ops.safe_detail(str(exc))}

    async def diagnostics(self, node_id: str, tail: int = 200) -> dict[str, Any]:
        record = await self.store.get(node_id)
        if not record:
            raise KeyError(node_id)
        host = await self.host_store.get(record.host_id)
        if not host:
            raise RemoteNodeError("节点所属物理主机不存在")
        agent, agent_error = await self._agent_diagnostics(record, host)
        output = await self.hosts.run_remote_script(
            record.host_id,
            image_ops.diagnostics_script(node_id, host.payload["docker_access"], tail),
            timeout=60, allow_maintenance=True,
            operation="node_diagnostics", node_id=node_id,
        )
        marker = "TASKHUB_CONTAINER_LOGS_BEGIN\n"
        header, _, logs = output.partition(marker)
        stats = next((line.partition("=")[2] for line in header.splitlines()
                      if line.startswith("TASKHUB_CONTAINER_STATS=")), "")
        operation_log = getattr(self.hosts, "operation_log", None)
        return {
            "node_id": node_id, "host_id": record.host_id, "location": "remote",
            "agent_logs": agent.get("agent_logs", []), "agent_error": agent_error,
            "container_logs": logs[-20_000:], "container_stats": stats,
            "resources": agent.get("resources", {}),
            "slots": agent.get("slots", record.payload.get("slots", 1)),
            "active_jobs": agent.get("active_jobs", 0),
            "last_error": record.status_reason
            if record.actual_state in {"error", "offline"} else "",
            "operations": operation_log.list(
                host_id=record.host_id, node_id=node_id, limit=100
            ) if operation_log else [],
        }

    async def _agent_diagnostics(self, record, host) -> tuple[dict, str]:
        try:
            return await node_helpers.agent_diagnostics(
                host.payload["address"], self._request(record),
                self._resolve_credential(record.node_id),
            ), ""
        except Exception as exc:
            return {}, image_ops.safe_detail(str(exc))
