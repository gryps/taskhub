from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

from taskhub_v2.config import Settings
from taskhub_v2.domain.topology import ProductionTopology, with_topology_digest


class TopologyStore(Protocol):
    async def save(self, topology: ProductionTopology) -> ProductionTopology: ...
    async def get(self, topology_id: str, version: int) -> ProductionTopology | None: ...
    async def list(self, project_id: str) -> list[ProductionTopology]: ...
    async def activate(self, topology: ProductionTopology) -> ProductionTopology: ...


def _stored(topology: ProductionTopology) -> ProductionTopology:
    return with_topology_digest(topology).model_copy(update={"updated_at": datetime.now(UTC)})


class MemoryTopologyStore:
    def __init__(self):
        self.records: dict[tuple[str, int], ProductionTopology] = {}

    async def save(self, topology: ProductionTopology) -> ProductionTopology:
        stored = _stored(topology)
        self.records[(stored.topology_id, stored.version)] = stored
        return stored

    async def get(self, topology_id: str, version: int) -> ProductionTopology | None:
        return self.records.get((topology_id, version))

    async def list(self, project_id: str) -> list[ProductionTopology]:
        return sorted(
            (item for item in self.records.values() if item.project_id == project_id),
            key=lambda item: item.version,
            reverse=True,
        )

    async def activate(self, topology: ProductionTopology) -> ProductionTopology:
        for key, current in list(self.records.items()):
            if current.project_id == topology.project_id and str(current.status) == "active":
                self.records[key] = _stored(current.model_copy(update={"status": "superseded"}))
        return await self.save(topology)


class PostgresTopologyStore:
    def __init__(self, pool):
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS taskhub_production_topology (
                topology_id TEXT NOT NULL, version INTEGER NOT NULL, project_id TEXT NOT NULL,
                status TEXT NOT NULL, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (topology_id, version))"""
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS taskhub_topology_project_version
                ON taskhub_production_topology (project_id, version DESC)"""
            )
            await connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS taskhub_topology_one_active
                ON taskhub_production_topology (project_id) WHERE status = 'active'"""
            )

    async def save(self, topology: ProductionTopology) -> ProductionTopology:
        stored = _stored(topology)
        async with self.pool.connection() as connection:
            await self._upsert(connection, stored)
        return stored

    async def get(self, topology_id: str, version: int) -> ProductionTopology | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT payload FROM taskhub_production_topology
                WHERE topology_id=%s AND version=%s""",
                (topology_id, version),
            )
            row = await cursor.fetchone()
        return ProductionTopology.model_validate(dict(row["payload"])) if row else None

    async def list(self, project_id: str) -> list[ProductionTopology]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT payload FROM taskhub_production_topology
                WHERE project_id=%s ORDER BY version DESC""",
                (project_id,),
            )
            return [
                ProductionTopology.model_validate(dict(row["payload"])) async for row in cursor
            ]

    async def activate(self, topology: ProductionTopology) -> ProductionTopology:
        stored = _stored(topology)
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                """UPDATE taskhub_production_topology SET status='superseded',
                payload=jsonb_set(payload, '{status}', '"superseded"'), updated_at=%s
                WHERE project_id=%s AND status='active'""",
                (datetime.now(UTC), topology.project_id),
            )
            await self._upsert(connection, stored)
        return stored

    @staticmethod
    async def _upsert(connection, topology: ProductionTopology) -> None:
        await connection.execute(
            """INSERT INTO taskhub_production_topology
            (topology_id, version, project_id, status, payload, updated_at)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (topology_id, version) DO UPDATE SET
            status=EXCLUDED.status, payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at""",
            (
                topology.topology_id,
                topology.version,
                topology.project_id,
                str(topology.status),
                json.dumps(topology.model_dump(mode="json")),
                topology.updated_at,
            ),
        )


@asynccontextmanager
async def topology_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield MemoryTopologyStore()
        return
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=3,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        store = PostgresTopologyStore(pool)
        await store.setup()
        yield store
