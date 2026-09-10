from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from taskhub_v2.config import Settings


@dataclass(frozen=True)
class ConfigurationRecord:
    scope: str
    payload: dict[str, Any]
    encrypted_secrets: dict[str, str]
    version: int
    applied_version: int
    updated_at: datetime
    applied_at: datetime | None = None


class MemoryConfigurationStore:
    def __init__(self):
        self.records: dict[str, ConfigurationRecord] = {}
        self.audit: list[dict[str, Any]] = []

    async def get(self, scope: str) -> ConfigurationRecord | None:
        return self.records.get(scope)

    async def save(
        self, scope: str, payload: dict[str, Any], encrypted_secrets: dict[str, str]
    ) -> ConfigurationRecord:
        previous = self.records.get(scope)
        record = ConfigurationRecord(
            scope=scope,
            payload=dict(payload),
            encrypted_secrets=dict(encrypted_secrets),
            version=(previous.version + 1) if previous else 1,
            applied_version=previous.applied_version if previous else 0,
            updated_at=datetime.now(UTC),
            applied_at=previous.applied_at if previous else None,
        )
        self.records[scope] = record
        return record

    async def mark_applied(self, scope: str, version: int) -> ConfigurationRecord | None:
        previous = self.records.get(scope)
        if not previous or previous.version != version:
            return previous
        record = ConfigurationRecord(
            scope=previous.scope,
            payload=previous.payload,
            encrypted_secrets=previous.encrypted_secrets,
            version=previous.version,
            applied_version=version,
            updated_at=previous.updated_at,
            applied_at=datetime.now(UTC),
        )
        self.records[scope] = record
        return record

    async def add_audit(
        self,
        *,
        operator: str,
        scope: str,
        action: str,
        parameter_summary: dict[str, Any],
        result: str,
    ) -> None:
        self.audit.append(
            {
                "event_id": str(uuid4()),
                "operator": operator,
                "scope": scope,
                "action": action,
                "parameter_summary": dict(parameter_summary),
                "result": result,
                "created_at": datetime.now(UTC),
            }
        )

    async def list_audit(self, scope: str | None = None, limit: int = 50) -> list[dict]:
        items = [item for item in self.audit if not scope or item["scope"] == scope]
        return list(reversed(items[-limit:]))


class PostgresConfigurationStore:
    def __init__(self, pool):
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS taskhub_managed_config (
                  scope TEXT PRIMARY KEY,
                  payload JSONB NOT NULL,
                  encrypted_secrets JSONB NOT NULL,
                  version INTEGER NOT NULL,
                  applied_version INTEGER NOT NULL DEFAULT 0,
                  updated_at TIMESTAMPTZ NOT NULL,
                  applied_at TIMESTAMPTZ)
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS taskhub_config_audit (
                  event_id TEXT PRIMARY KEY,
                  operator TEXT NOT NULL,
                  scope TEXT NOT NULL,
                  action TEXT NOT NULL,
                  parameter_summary JSONB NOT NULL,
                  result TEXT NOT NULL,
                  created_at TIMESTAMPTZ NOT NULL)
                """
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS taskhub_config_audit_scope_created
                   ON taskhub_config_audit (scope, created_at DESC)"""
            )

    async def get(self, scope: str) -> ConfigurationRecord | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM taskhub_managed_config WHERE scope=%s", (scope,)
            )
            row = await cursor.fetchone()
        return _record(row) if row else None

    async def save(
        self, scope: str, payload: dict[str, Any], encrypted_secrets: dict[str, str]
    ) -> ConfigurationRecord:
        now = datetime.now(UTC)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO taskhub_managed_config
                  (scope, payload, encrypted_secrets, version, applied_version, updated_at)
                VALUES (%s, %s::jsonb, %s::jsonb, 1, 0, %s)
                ON CONFLICT (scope) DO UPDATE SET
                  payload=EXCLUDED.payload,
                  encrypted_secrets=EXCLUDED.encrypted_secrets,
                  version=taskhub_managed_config.version + 1,
                  updated_at=EXCLUDED.updated_at
                RETURNING *
                """,
                (scope, json.dumps(payload), json.dumps(encrypted_secrets), now),
            )
            row = await cursor.fetchone()
        return _record(row)

    async def mark_applied(self, scope: str, version: int) -> ConfigurationRecord | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE taskhub_managed_config
                SET applied_version=version, applied_at=%s
                WHERE scope=%s AND version=%s
                RETURNING *
                """,
                (datetime.now(UTC), scope, version),
            )
            row = await cursor.fetchone()
        return _record(row) if row else await self.get(scope)

    async def add_audit(
        self,
        *,
        operator: str,
        scope: str,
        action: str,
        parameter_summary: dict[str, Any],
        result: str,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO taskhub_config_audit
                  (event_id, operator, scope, action, parameter_summary, result, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    str(uuid4()),
                    operator,
                    scope,
                    action,
                    json.dumps(parameter_summary),
                    result,
                    datetime.now(UTC),
                ),
            )

    async def list_audit(self, scope: str | None = None, limit: int = 50) -> list[dict]:
        clause = " WHERE scope=%s" if scope else ""
        parameters: list[Any] = [scope] if scope else []
        parameters.append(limit)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                f"SELECT * FROM taskhub_config_audit{clause} "
                "ORDER BY created_at DESC LIMIT %s",
                parameters,
            )
            return [dict(row) async for row in cursor]


def _record(row) -> ConfigurationRecord:
    values = dict(row)
    return ConfigurationRecord(
        scope=values["scope"],
        payload=dict(values["payload"]),
        encrypted_secrets=dict(values["encrypted_secrets"]),
        version=values["version"],
        applied_version=values["applied_version"],
        updated_at=values["updated_at"],
        applied_at=values["applied_at"],
    )


@asynccontextmanager
async def configuration_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield MemoryConfigurationStore()
        return

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        store = PostgresConfigurationStore(pool)
        await store.setup()
        yield store
