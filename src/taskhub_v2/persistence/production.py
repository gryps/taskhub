from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

from taskhub_v2.config import Settings
from taskhub_v2.domain.dag import ExecutionBatch
from taskhub_v2.domain.production import (
    ProductDecision,
    ProductionObject,
    ProductionTask,
    Requirement,
    immutable_content,
    object_identity,
    production_object_adapter,
    validate_transition,
    with_content_digest,
)
from taskhub_v2.domain.project_contract import ProjectContract


class ProductionObjectConflictError(RuntimeError):
    pass


class ProductionStore(Protocol):
    async def save(self, record: ProductionObject) -> ProductionObject: ...

    async def get(
        self, object_type: str, object_id: str, revision: str
    ) -> ProductionObject | None: ...

    async def list(
        self, *, project_id: str, object_type: str | None = None
    ) -> list[ProductionObject]: ...


def _state(record: ProductionObject) -> str:
    return str(record.status)


def _validate_replacement(previous: ProductionObject, record: ProductionObject) -> None:
    try:
        validate_transition(record.object_type, _state(previous), _state(record))
    except ValueError as error:
        raise ProductionObjectConflictError(str(error)) from error
    if isinstance(previous, Requirement) and isinstance(record, Requirement):
        previous_supplements = [item.model_dump() for item in previous.supplements]
        target_supplements = [item.model_dump() for item in record.supplements]
        original_changed = (
            previous.original_text != record.original_text
            or previous.attachments != record.attachments
            or previous.submitted_at != record.submitted_at
            or target_supplements[: len(previous_supplements)] != previous_supplements
        )
        if original_changed:
            raise ProductionObjectConflictError(
                "requirement originals are immutable and supplements are append-only"
            )
        return
    if isinstance(previous, ProductDecision) and isinstance(record, ProductDecision):
        fixed_fields_changed = (
            previous.requirement_id != record.requirement_id
            or previous.spec_id != record.spec_id
            or previous.spec_version != record.spec_version
            or previous.questions != record.questions
        )
        if fixed_fields_changed:
            raise ProductionObjectConflictError("product decision questions are immutable")
        if _state(previous) != "pending" and immutable_content(previous) != immutable_content(
            record
        ):
            raise ProductionObjectConflictError("resolved product decisions are immutable")
        return
    if isinstance(previous, ProjectContract) and isinstance(record, ProjectContract):
        excluded = {
            "status",
            "created_at",
            "updated_at",
            "created_by",
            "content_digest",
            "approved_by",
            "approved_at",
        }
        if previous.model_dump(mode="json", exclude=excluded) != record.model_dump(
            mode="json", exclude=excluded
        ):
            raise ProductionObjectConflictError(
                "project contract content is immutable during lifecycle transitions"
            )
        return
    if isinstance(previous, ProductionTask) and isinstance(record, ProductionTask):
        runtime_fields = {
            "status",
            "waiting_reasons",
            "assigned_node_id",
            "batch_id",
            "created_at",
            "updated_at",
            "created_by",
            "content_digest",
        }
        if previous.model_dump(mode="json", exclude=runtime_fields) != record.model_dump(
            mode="json", exclude=runtime_fields
        ):
            raise ProductionObjectConflictError("production task definition is immutable")
        return
    if isinstance(previous, ExecutionBatch) and isinstance(record, ExecutionBatch):
        fixed_fields = {"project_id", "batch_id", "plan_id", "plan_version", "sequence", "task_ids"}
        if any(getattr(previous, field) != getattr(record, field) for field in fixed_fields):
            raise ProductionObjectConflictError("execution batch definition is immutable")
        return
    mutable_states = {
        "product_spec": {"draft", "in_review"},
        "project_contract": {"draft", "in_review"},
        "execution_plan": {"draft", "validating"},
        "task": {"pending"},
        "change_request": {"proposed"},
        "capability_pack": {"draft"},
    }.get(record.object_type, set())
    changes_allowed = record.object_type == "task_attempt" or (
        _state(previous) in mutable_states and _state(record) in mutable_states
    )
    if immutable_content(previous) != immutable_content(record) and not changes_allowed:
        raise ProductionObjectConflictError(
            "versioned production object content is immutable; create a new version"
        )


class MemoryProductionStore:
    def __init__(self):
        self.records: dict[tuple[str, str, str], ProductionObject] = {}

    async def save(self, record: ProductionObject) -> ProductionObject:
        record = with_content_digest(record)
        key = object_identity(record)
        previous = self.records.get(key)
        if previous:
            _validate_replacement(previous, record)
            record = record.model_copy(
                update={"created_at": previous.created_at, "created_by": previous.created_by}
            )
        stored = record.model_copy(update={"updated_at": datetime.now(UTC)})
        self.records[key] = stored
        return stored

    async def get(self, object_type: str, object_id: str, revision: str) -> ProductionObject | None:
        return self.records.get((object_type, object_id, revision))

    async def list(
        self, *, project_id: str, object_type: str | None = None
    ) -> list[ProductionObject]:
        records = [
            item
            for item in self.records.values()
            if item.project_id == project_id
            and (object_type is None or item.object_type == object_type)
        ]
        return sorted(records, key=lambda item: object_identity(item))


class PostgresProductionStore:
    MIGRATION_ID = "production-baseline-v1"

    def __init__(self, pool):
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS taskhub_schema_migration (
                  migration_id TEXT PRIMARY KEY,
                  applied_at TIMESTAMPTZ NOT NULL)
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS taskhub_production_object (
                  object_type TEXT NOT NULL,
                  object_id TEXT NOT NULL,
                  revision TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  payload JSONB NOT NULL,
                  created_at TIMESTAMPTZ NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL,
                  PRIMARY KEY (object_type, object_id, revision))
                """
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS taskhub_production_object_project_type
                   ON taskhub_production_object (project_id, object_type, updated_at DESC)"""
            )
            await connection.execute(
                """INSERT INTO taskhub_schema_migration (migration_id, applied_at)
                   VALUES (%s, %s) ON CONFLICT (migration_id) DO NOTHING""",
                (self.MIGRATION_ID, datetime.now(UTC)),
            )

    async def save(self, record: ProductionObject) -> ProductionObject:
        record = with_content_digest(record)
        object_type, object_id, revision = object_identity(record)
        now = datetime.now(UTC)
        stored = record.model_copy(update={"updated_at": now})
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT payload FROM taskhub_production_object
                   WHERE object_type=%s AND object_id=%s AND revision=%s""",
                (object_type, object_id, revision),
            )
            row = await cursor.fetchone()
            if row:
                previous = production_object_adapter.validate_python(dict(row["payload"]))
                _validate_replacement(previous, record)
                record = record.model_copy(
                    update={"created_at": previous.created_at, "created_by": previous.created_by}
                )
                stored = record.model_copy(update={"updated_at": now})
            await connection.execute(
                """
                INSERT INTO taskhub_production_object
                  (object_type, object_id, revision, project_id, status, payload,
                   created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (object_type, object_id, revision) DO UPDATE SET
                  status=EXCLUDED.status, payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at
                """,
                (
                    object_type,
                    object_id,
                    revision,
                    record.project_id,
                    _state(record),
                    json.dumps(stored.model_dump(mode="json")),
                    record.created_at,
                    now,
                ),
            )
        return stored

    async def get(self, object_type: str, object_id: str, revision: str) -> ProductionObject | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT payload FROM taskhub_production_object
                   WHERE object_type=%s AND object_id=%s AND revision=%s""",
                (object_type, object_id, revision),
            )
            row = await cursor.fetchone()
        return production_object_adapter.validate_python(dict(row["payload"])) if row else None

    async def list(
        self, *, project_id: str, object_type: str | None = None
    ) -> list[ProductionObject]:
        clause = " AND object_type=%s" if object_type else ""
        parameters = [project_id, object_type] if object_type else [project_id]
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT payload FROM taskhub_production_object WHERE project_id=%s"
                f"{clause} ORDER BY object_type, object_id, revision",
                parameters,
            )
            return [
                production_object_adapter.validate_python(dict(row["payload"]))
                async for row in cursor
            ]


@asynccontextmanager
async def production_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield MemoryProductionStore()
        return

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=3,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        store = PostgresProductionStore(pool)
        await store.setup()
        yield store
