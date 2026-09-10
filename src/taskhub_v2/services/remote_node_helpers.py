import asyncio
from typing import Any

import httpx

from taskhub_v2.domain.models import NodeDefinition
from taskhub_v2.domain.remote_nodes import RemoteNodeCreate
from taskhub_v2.services.containers import ROLE_WORKLOADS


async def wait_healthy(address: str, request: RemoteNodeCreate, node_token: str) -> None:
    url = f"http://{address}:{request.host_port}/api/health"
    last_error = "Node Agent 尚未响应"
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        for _ in range(10):
            try:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {node_token}"}
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("node_id") == request.node_id:
                    return
                last_error = "Node Agent 返回了不匹配的节点 ID"
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc)[:200]
            await asyncio.sleep(2)
    raise RuntimeError(f"容器已启动，但健康检查未通过：{last_error}")


async def agent_diagnostics(
    address: str, request: RemoteNodeCreate, node_token: str
) -> dict[str, Any]:
    url = f"http://{address}:{request.host_port}/api/diagnostics"
    async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
        response = await client.get(
            url, headers={"Authorization": f"Bearer {node_token}"}
        )
        response.raise_for_status()
        payload = response.json()
    if payload.get("node_id") != request.node_id:
        raise RuntimeError("Node Agent 返回了不匹配的节点 ID")
    return payload


def node_definition(address: str, request: RemoteNodeCreate) -> NodeDefinition:
    return NodeDefinition(
        id=request.node_id,
        kind="remote",
        url=f"http://{address}:{request.host_port}",
        slots=request.slots,
        workloads=ROLE_WORKLOADS[request.role],
    )


def remote_node_view(record, credential: dict | None = None) -> dict[str, Any]:
    view = {
        **record.payload,
        "container_id": record.container_id,
        "image_digest": record.image_digest,
        "desired_state": record.desired_state,
        "actual_state": record.actual_state,
        "status_reason": record.status_reason,
        "updated_at": record.updated_at.isoformat(),
    }
    if credential is not None:
        view["credential"] = credential
    return view
