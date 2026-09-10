from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from taskhub_v2.config import Settings


@dataclass(frozen=True)
class PhysicalHostRecord:
    host_id: str
    payload: dict[str, Any]
    encrypted_private_key: str
    host_key: str
    fingerprint: str
    status: str
    facts: dict[str, Any]
    status_reason: str
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime


class MemoryHostStore:
    def __init__(self):
        self.hosts: dict[str, PhysicalHostRecord] = {}

    async def list(self) -> list[PhysicalHostRecord]:
        return sorted(self.hosts.values(), key=lambda item: item.host_id)

    async def get(self, host_id: str) -> PhysicalHostRecord | None:
        return self.hosts.get(host_id)

    async def save(self, record: PhysicalHostRecord) -> PhysicalHostRecord:
        self.hosts[record.host_id] = record
        return record


class PostgresHostStore:
    def __init__(self, pool):
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS taskhub_physical_host (
                  host_id TEXT PRIMARY KEY,
                  payload JSONB NOT NULL,
                  encrypted_private_key TEXT NOT NULL,
                  host_key TEXT NOT NULL,
                  fingerprint TEXT NOT NULL,
                  status TEXT NOT NULL,
                  facts JSONB NOT NULL,
                  status_reason TEXT NOT NULL,
                  created_at TIMESTAMPTZ NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL,
                  last_checked_at TIMESTAMPTZ NOT NULL)
                """
            )

    async def list(self) -> list[PhysicalHostRecord]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM taskhub_physical_host ORDER BY host_id"
            )
            return [_record(row) async for row in cursor]

    async def get(self, host_id: str) -> PhysicalHostRecord | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM taskhub_physical_host WHERE host_id=%s", (host_id,)
            )
            row = await cursor.fetchone()
        return _record(row) if row else None

    async def save(self, record: PhysicalHostRecord) -> PhysicalHostRecord:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO taskhub_physical_host
                  (host_id, payload, encrypted_private_key, host_key, fingerprint,
                   status, facts, status_reason, created_at, updated_at, last_checked_at)
                VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (host_id) DO UPDATE SET
                  payload=EXCLUDED.payload,
                  encrypted_private_key=EXCLUDED.encrypted_private_key,
                  host_key=EXCLUDED.host_key,
                  fingerprint=EXCLUDED.fingerprint,
                  status=EXCLUDED.status,
                  facts=EXCLUDED.facts,
                  status_reason=EXCLUDED.status_reason,
                  updated_at=EXCLUDED.updated_at,
                  last_checked_at=EXCLUDED.last_checked_at
                RETURNING *
                """,
                (
                    record.host_id,
                    json.dumps(record.payload),
                    record.encrypted_private_key,
                    record.host_key,
                    record.fingerprint,
                    record.status,
                    json.dumps(record.facts),
                    record.status_reason,
                    record.created_at,
                    record.updated_at,
                    record.last_checked_at,
                ),
            )
            row = await cursor.fetchone()
        return _record(row)


def new_host_record(**values) -> PhysicalHostRecord:
    now = datetime.now(UTC)
    return PhysicalHostRecord(created_at=now, updated_at=now, last_checked_at=now, **values)


def _record(row) -> PhysicalHostRecord:
    values = dict(row)
    values["payload"] = dict(values["payload"])
    values["facts"] = dict(values["facts"])
    return PhysicalHostRecord(**values)


@asynccontextmanager
async def physical_host_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield MemoryHostStore()
        return

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=3,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        store = PostgresHostStore(pool)
        await store.setup()
        yield store
