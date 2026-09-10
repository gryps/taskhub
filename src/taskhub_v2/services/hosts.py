from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from taskhub_v2.domain.hosts import HostConnection, PhysicalHostCreate
from taskhub_v2.persistence.hosts import PhysicalHostRecord, new_host_record
from taskhub_v2.security import SecretCipher
from taskhub_v2.services.host_connection import (
    HostAdmissionError,
    probe_host,
    safe_error,
)
from taskhub_v2.services.host_connection import (
    run_remote_script as execute_remote_script,
)
from taskhub_v2.services.ssh_image_transfer import (
    SSHImageTransferError,
    load_docker_image,
)


class PhysicalHostService:
    def __init__(
        self, store, configuration_store, cipher: SecretCipher | None, callback: str,
        *, operation_log=None, monitor_interval_seconds: int = 60,
    ):
        self.store = store
        self.configuration_store = configuration_store
        self.cipher = cipher
        self.callback = callback
        self.operation_log = operation_log
        self.monitor_interval_seconds = max(15, monitor_interval_seconds)
        self._monitor_task: asyncio.Task | None = None

    async def list(self) -> dict[str, Any]:
        return {"hosts": [_host_view(item) for item in await self.store.list()]}

    async def probe(self, request: HostConnection) -> dict[str, Any]:
        return await asyncio.to_thread(self._probe, request)

    async def save(self, request: PhysicalHostCreate) -> dict[str, Any]:
        if not self.cipher:
            raise HostAdmissionError("尚未配置主机凭据加密根密钥")
        outcome = await self.probe(request)
        if outcome["status"] != "available":
            raise HostAdmissionError(outcome["detail"])
        existing = await self.store.get(request.host_id)
        payload = request.model_dump(exclude={"private_key", "expected_fingerprint"}, mode="json")
        payload["operational_state"] = "active"
        record = new_host_record(
            host_id=request.host_id,
            payload=payload,
            encrypted_private_key=self.cipher.encrypt(request.private_key.get_secret_value()),
            host_key=outcome["host_key"],
            fingerprint=outcome["fingerprint"],
            status="available",
            facts=outcome["facts"],
            status_reason="准入检测通过",
        )
        if existing:
            record = replace(record, created_at=existing.created_at)
        saved = await self.store.save(record)
        await self._audit("admit", request.host_id, "passed")
        return _host_view(saved)

    async def check(self, host_id: str) -> dict[str, Any]:
        record = await self.store.get(host_id)
        if not record:
            raise KeyError(host_id)
        if not self.cipher:
            raise HostAdmissionError("主机凭据加密根密钥不可用")
        if record.payload.get("operational_state") == "disabled":
            return _host_view(record)
        request = HostConnection(
            **record.payload,
            private_key=self.cipher.decrypt(record.encrypted_private_key),
            expected_fingerprint=record.fingerprint,
        )
        try:
            outcome = await self.probe(request)
            checked_at = _now()
            state = record.payload.get("operational_state", "active")
            alerts = _resource_alerts(outcome["facts"])
            status = "available" if state == "active" and not alerts else state
            if state == "active" and alerts:
                status = "degraded"
            reason = "重新检测通过" if not alerts else "；".join(alerts)
            if state != "active":
                reason = _maintenance_reason(state)
            updated = replace(
                record,
                host_key=outcome["host_key"],
                facts=outcome["facts"],
                status=status,
                status_reason=reason,
                updated_at=checked_at,
                last_checked_at=checked_at,
            )
            result = "passed"
        except HostAdmissionError as exc:
            detail = _safe_error(exc)
            updated = replace(
                record,
                status="blocked" if "指纹不一致" in detail else "degraded",
                status_reason=detail,
                last_checked_at=_now(),
            )
            result = "failed"
        saved = await self.store.save(updated)
        await self._audit("check", host_id, result)
        return _host_view(saved)

    async def set_operational_state(self, host_id: str, state: str) -> dict[str, Any]:
        if state not in {"active", "draining", "maintenance", "disabled"}:
            raise ValueError("不支持的主机维护状态")
        record = await self.store.get(host_id)
        if not record:
            raise KeyError(host_id)
        payload = {**record.payload, "operational_state": state}
        status = "available" if state == "active" else state
        saved = await self.store.save(
            replace(
                record, payload=payload, status=status,
                status_reason=_maintenance_reason(state),
            )
        )
        await self._audit(f"host_{state}", host_id, "passed")
        if self.operation_log:
            self.operation_log.record("host_state", "passed", host_id=host_id, state=state)
        if state == "active":
            return await self.check(host_id)
        return _host_view(saved)

    def start_monitoring(self) -> None:
        if not self._monitor_task or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self.monitor_interval_seconds)
            hosts = await self.store.list()
            await asyncio.gather(
                *(self.check(item.host_id) for item in hosts), return_exceptions=True
            )

    async def close(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)

    async def run_remote_script(
        self, host_id: str, script: str, timeout: int = 120, *,
        allow_maintenance: bool = False, operation: str = "ssh_docker", node_id: str = "",
    ) -> str:
        record = await self.store.get(host_id)
        if not record:
            raise KeyError(host_id)
        allowed = (
            {"available", "degraded", "draining", "maintenance", "disabled"}
            if allow_maintenance
            else {"available"}
        )
        if record.status not in allowed:
            raise HostAdmissionError(f"物理主机 {host_id} 当前不可用：{record.status_reason}")
        if not self.cipher:
            raise HostAdmissionError("主机凭据加密根密钥不可用")
        started = time.monotonic()
        try:
            output = await asyncio.to_thread(
                execute_remote_script, record,
                self.cipher.decrypt(record.encrypted_private_key), script, timeout,
            )
        except Exception as exc:
            if self.operation_log:
                self.operation_log.record(
                    operation, "failed", host_id=host_id, node_id=node_id,
                    duration_ms=int((time.monotonic() - started) * 1000), error=_safe_error(exc),
                )
            raise
        if self.operation_log:
            self.operation_log.record(
                operation, "passed", host_id=host_id, node_id=node_id,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        return output

    async def load_remote_image(self, host_id: str, image_path: Path, timeout: int = 1800) -> str:
        record = await self.store.get(host_id)
        if not record:
            raise KeyError(host_id)
        if record.status != "available":
            raise HostAdmissionError(f"物理主机 {host_id} 当前不可用：{record.status_reason}")
        if not self.cipher:
            raise HostAdmissionError("主机凭据加密根密钥不可用")
        try:
            return await asyncio.to_thread(
                load_docker_image,
                record,
                self.cipher.decrypt(record.encrypted_private_key),
                image_path,
                timeout,
            )
        except SSHImageTransferError as exc:
            raise HostAdmissionError(str(exc)) from exc

    def _probe(self, request: HostConnection) -> dict[str, Any]:
        return probe_host(request, self.callback)

    async def _audit(self, action: str, host_id: str, result: str) -> None:
        await self.configuration_store.add_audit(
            operator="admin",
            scope="physical_hosts",
            action=action,
            parameter_summary={"host_id": host_id},
            result=result,
        )


def _host_view(record: PhysicalHostRecord) -> dict[str, Any]:
    labels = {
        "available": "可用", "degraded": "资源告警", "unreachable": "不可达",
        "draining": "排空中", "maintenance": "维护中", "disabled": "已停用",
        "blocked": "指纹阻断",
    }
    payload = dict(record.payload)
    # Records admitted by the earlier Alpha used a free-text purpose field.
    payload.pop("purpose", None)
    payload.setdefault("allowed_roles", ["execution", "test", "preproduction"])
    payload.setdefault("operational_state", "active")
    return {
        **payload,
        "fingerprint": record.fingerprint,
        "status": record.status,
        "status_label": labels.get(record.status, record.status),
        "facts": record.facts,
        "alerts": _resource_alerts(record.facts),
        "status_reason": record.status_reason,
        "private_key_configured": bool(record.encrypted_private_key),
        "last_checked_at": record.last_checked_at.isoformat(),
    }


def _resource_alerts(facts: dict[str, Any]) -> list[str]:
    alerts = []
    memory = facts.get("memory_bytes") or 0
    available_memory = facts.get("memory_available_bytes") or memory
    disk = facts.get("disk_total_bytes") or 0
    available_disk = facts.get("disk_available_bytes") or disk
    cpu = facts.get("cpu_count") or 1
    if memory and available_memory / memory < 0.1:
        alerts.append("可用内存低于 10%")
    if disk and (available_disk / disk < 0.1 or available_disk < 10 * 1024**3):
        alerts.append("可用磁盘低于安全阈值")
    if (facts.get("load_average_1m") or 0) > cpu * 1.5:
        alerts.append("CPU 负载持续偏高")
    return alerts


def _maintenance_reason(state: str) -> str:
    return {
        "active": "主机已重新启用，等待健康检测",
        "draining": "主机正在排空，不再分配新任务",
        "maintenance": "主机处于维护模式，不参与调度",
        "disabled": "主机已停用，不执行自动检测或调度",
    }[state]


def _safe_error(error: Exception) -> str:
    return safe_error(error)


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
