from __future__ import annotations

import asyncio
import hashlib
import io
import json
import platform
import zipfile
from datetime import UTC, datetime
from typing import Any

from taskhub_v2.services.operational_log import sanitize


class SystemDiagnosticsService:
    def __init__(
        self, settings, hosts, remote_nodes, container_manager, scheduler, operation_log
    ):
        self.settings = settings
        self.hosts = hosts
        self.remote_nodes = remote_nodes
        self.container_manager = container_manager
        self.scheduler = scheduler
        self.operation_log = operation_log

    async def node(self, node_id: str, tail: int = 200) -> dict[str, Any]:
        remote = (
            await self.remote_nodes.store.get(node_id)
            if self.remote_nodes is not None
            else None
        )
        if remote:
            result = await self.remote_nodes.diagnostics(node_id, tail)
        else:
            result = await asyncio.to_thread(self.container_manager.diagnostics, node_id, tail)
            logs = result.get("container_logs", "")
            result.update(
                {
                    "node_id": node_id,
                    "host_id": "seed-local",
                    "location": "local",
                    "agent_logs": result.get("agent_logs") or [
                        {"created_at": "", "event": line, "result": "info"}
                        for line in logs.splitlines()[-50:]
                    ],
                    "operations": self.operation_log.list(node_id=node_id, limit=100),
                    "last_error": result.get("agent_error", ""),
                }
            )
        statuses = await self.scheduler.status()
        status = next((item for item in statuses if item.get("node_id") == node_id), None)
        if status:
            result["slots"] = status.get("slots", result.get("slots", 1))
            result["active_jobs"] = status.get("active", result.get("active_jobs", 0))
            result["scheduler"] = status
            if status.get("status") != "ok":
                result["last_error"] = result.get("last_error") or status.get("detail", "")
        return sanitize(result)

    async def export(self) -> tuple[bytes, str]:
        created = datetime.now(UTC).isoformat()
        # Keep the legacy archive members so older support tooling can still
        # consume diagnostics, but single-Seed deployments no longer start or
        # query the retired SSH host/remote-node services.
        hosts = await self.hosts.list() if self.hosts is not None else {"hosts": []}
        remote_nodes = (
            await self.remote_nodes.list()
            if self.remote_nodes is not None
            else {"nodes": []}
        )
        scheduler = await self.scheduler.status()
        try:
            containers = await asyncio.to_thread(self.container_manager.list)
        except Exception as exc:
            containers = [{"error": str(exc)}]
        payloads = {
            "manifest.json": {
                "created_at": created,
                "format": "taskhub-sanitized-diagnostics-v1",
                "version": "0.1.0-alpha",
            },
            "system.json": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "environment": self.settings.env,
                "checkpointer": self.settings.checkpointer,
            },
            "hosts.json": hosts,
            "remote-nodes.json": remote_nodes,
            "scheduler.json": scheduler,
            "local-containers.json": containers,
            "operations.json": self.operation_log.list(limit=500),
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, payload in payloads.items():
                clean = sanitize(payload, addresses=True)
                bundle.writestr(name, json.dumps(clean, ensure_ascii=False, indent=2))
        content = archive.getvalue()
        return content, hashlib.sha256(content).hexdigest()
