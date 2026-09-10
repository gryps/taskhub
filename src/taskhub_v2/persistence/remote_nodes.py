from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from taskhub_v2.config import Settings


@dataclass(frozen=True)
class RemoteNodeRecord:
    node_id: str
    host_id: str
    payload: dict[str, Any]
    container_id: str
    image_digest: str
    desired_state: str
    actual_state: str
    status_reason: str
    created_at: datetime
    updated_at: datetime


class MemoryRemoteNodeStore:
    def __init__(self):
        self.nodes: dict[str, RemoteNodeRecord] = {}

    async def list(self) -> list[RemoteNodeRecord]:
        return sorted(self.nodes.values(), key=lambda item: item.node_id)

    async def get(self, node_id: str) -> RemoteNodeRecord | None:
        return self.nodes.get(node_id)

    async def save(self, record: RemoteNodeRecord) -> RemoteNodeRecord:
        record = replace(record, updated_at=datetime.now(UTC))
        self.nodes[record.node_id] = record
        return record

    async def delete(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)


class PostgresRemoteNodeStore:
    def __init__(self, pool):
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS taskhub_remote_node (
                  node_id TEXT PRIMARY KEY,
                  host_id TEXT NOT NULL,
                  payload JSONB NOT NULL,
                  container_id TEXT NOT NULL,
                  image_digest TEXT NOT NULL,
                  desired_state TEXT NOT NULL,
                  actual_state TEXT NOT NULL,
                  status_reason TEXT NOT NULL,
                  created_at TIMESTAMPTZ NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL)
                """
            )

    async def list(self) -> list[RemoteNodeRecord]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT * FROM taskhub_remote_node ORDER BY node_id")
            return [_record(row) async for row in cursor]

    async def get(self, node_id: str) -> RemoteNodeRecord | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM taskhub_remote_node WHERE node_id=%s", (node_id,)
            )
            row = await cursor.fetchone()
        return _record(row) if row else None

    async def save(self, record: RemoteNodeRecord) -> RemoteNodeRecord:
        updated_at = datetime.now(UTC)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO taskhub_remote_node
                  (node_id, host_id, payload, container_id, image_digest, desired_state,
                   actual_state, status_reason, created_at, updated_at)
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (node_id) DO UPDATE SET
                  host_id=EXCLUDED.host_id, payload=EXCLUDED.payload,
                  container_id=EXCLUDED.container_id, image_digest=EXCLUDED.image_digest,
                  desired_state=EXCLUDED.desired_state, actual_state=EXCLUDED.actual_state,
                  status_reason=EXCLUDED.status_reason, updated_at=EXCLUDED.updated_at
                RETURNING *
                """,
                (
                    record.node_id,
                    record.host_id,
                    json.dumps(record.payload),
                    record.container_id,
                    record.image_digest,
                    record.desired_state,
                    record.actual_state,
                    record.status_reason,
                    record.created_at,
                    updated_at,
                ),
            )
            row = await cursor.fetchone()
        return _record(row)

    async def delete(self, node_id: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute("DELETE FROM taskhub_remote_node WHERE node_id=%s", (node_id,))


def new_remote_node_record(**values) -> RemoteNodeRecord:
    now = datetime.now(UTC)
    return RemoteNodeRecord(created_at=now, updated_at=now, **values)


def _record(row) -> RemoteNodeRecord:
    values = dict(row)
    values["payload"] = dict(values["payload"])
    return RemoteNodeRecord(**values)


@asynccontextmanager
async def remote_node_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield MemoryRemoteNodeStore()
        return

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=3,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        store = PostgresRemoteNodeStore(pool)
        await store.setup()
        yield store
