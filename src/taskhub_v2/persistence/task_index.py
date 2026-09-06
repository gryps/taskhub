from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from taskhub_v2.config import Settings
from taskhub_v2.domain.models import TaskPage, TaskSummary


class MemoryTaskIndex:
    def __init__(self):
        self._items: dict[str, TaskSummary] = {}

    async def upsert(self, values: dict[str, Any], production_line: str | None = None):
        now = datetime.now(UTC)
        previous = self._items.get(values["run_id"])
        item = TaskSummary(
            run_id=values["run_id"],
            requirement_summary=_summary(values.get("requirement", "")),
            project_id=values["project_id"],
            production_line=production_line or values.get("production_line")
            or (previous.production_line if previous else "default"),
            stage=values["current_stage"],
            status=values["status"],
            blocking_reason=values.get("blocking_reason"),
            pending_action=values.get("pending_action"),
            created_at=previous.created_at if previous else now,
            updated_at=now,
            archived_at=previous.archived_at if previous else None,
            rebound_project_id=previous.rebound_project_id if previous else None,
        )
        if previous and item.model_dump(exclude={"updated_at"}) == previous.model_dump(
            exclude={"updated_at"}
        ):
            return previous
        self._items[item.run_id] = item
        return item

    async def get(self, run_id: str) -> TaskSummary | None:
        return self._items.get(run_id)

    async def list(self, *, page=1, page_size=50, **filters) -> TaskPage:
        include_archived = filters.pop("include_archived", False)
        items = [
            item for item in self._items.values()
            if (include_archived or item.archived_at is None)
            and all(not value or getattr(item, key) == value for key, value in filters.items())
        ]
        items.sort(key=lambda item: (item.updated_at, item.run_id), reverse=True)
        start = (page - 1) * page_size
        return TaskPage(items=items[start:start + page_size], total=len(items), page=page,
                        page_size=page_size)

    async def archive(self, run_id: str) -> TaskSummary | None:
        item = self._items.get(run_id)
        if item and item.archived_at is None:
            now = datetime.now(UTC)
            item = item.model_copy(update={"archived_at": now, "updated_at": now})
            self._items[run_id] = item
        return item

    async def rebind(self, run_id: str, project_id: str) -> TaskSummary | None:
        item = self._items.get(run_id)
        if item:
            item = item.model_copy(
                update={"rebound_project_id": project_id, "updated_at": datetime.now(UTC)}
            )
            self._items[run_id] = item
        return item


class PostgresTaskIndex:
    def __init__(self, pool):
        self.pool = pool

    async def setup(self):
        async with self.pool.connection() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS taskhub_task_index (
                  run_id TEXT PRIMARY KEY, requirement_summary TEXT NOT NULL,
                  project_id TEXT NOT NULL, production_line TEXT NOT NULL,
                  stage TEXT NOT NULL, status TEXT NOT NULL, blocking_reason JSONB,
                  pending_action JSONB, created_at TIMESTAMPTZ NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL)
            """)
            await connection.execute(
                "ALTER TABLE taskhub_task_index ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ"
            )
            await connection.execute(
                "ALTER TABLE taskhub_task_index ADD COLUMN IF NOT EXISTS rebound_project_id TEXT"
            )
            for column in ("project_id", "production_line", "status", "stage", "updated_at"):
                await connection.execute(
                    f"CREATE INDEX IF NOT EXISTS taskhub_task_index_{column} "
                    f"ON taskhub_task_index ({column})"
                )

    async def upsert(self, values: dict[str, Any], production_line: str | None = None):
        production_line = production_line or values.get("production_line")
        now = datetime.now(UTC)
        async with self.pool.connection() as connection:
            await connection.execute("""
                INSERT INTO taskhub_task_index
                  (run_id, requirement_summary, project_id, production_line, stage, status,
                   blocking_reason, pending_action, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                  requirement_summary=EXCLUDED.requirement_summary,
                  project_id=EXCLUDED.project_id,
                  production_line=COALESCE(%s, taskhub_task_index.production_line),
                  stage=EXCLUDED.stage, status=EXCLUDED.status,
                  blocking_reason=EXCLUDED.blocking_reason,
                  pending_action=EXCLUDED.pending_action, updated_at=EXCLUDED.updated_at
                WHERE (taskhub_task_index.requirement_summary, taskhub_task_index.project_id,
                       taskhub_task_index.production_line, taskhub_task_index.stage,
                       taskhub_task_index.status, taskhub_task_index.blocking_reason,
                       taskhub_task_index.pending_action) IS DISTINCT FROM
                      (EXCLUDED.requirement_summary, EXCLUDED.project_id,
                       COALESCE(%s, taskhub_task_index.production_line), EXCLUDED.stage,
                       EXCLUDED.status, EXCLUDED.blocking_reason, EXCLUDED.pending_action)
            """, (values["run_id"], _summary(values.get("requirement", "")), values["project_id"],
                  production_line or "default", values["current_stage"], values["status"],
                  json.dumps(values.get("blocking_reason")),
                  json.dumps(values.get("pending_action")), now, now, production_line,
                  production_line))
        return await self.get(values["run_id"])

    async def get(self, run_id: str) -> TaskSummary | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM taskhub_task_index WHERE run_id=%s", (run_id,)
            )
            row = await cursor.fetchone()
        return TaskSummary.model_validate(dict(row)) if row else None

    async def list(self, *, page=1, page_size=50, **filters) -> TaskPage:
        include_archived = filters.pop("include_archived", False)
        clauses, params = ([] if include_archived else ["archived_at IS NULL"]), []
        for key, value in filters.items():
            if value:
                clauses.append(f"{key}=%s")
                params.append(str(value))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.pool.connection() as connection:
            count = await connection.execute(
                f"SELECT count(*) AS count FROM taskhub_task_index{where}", params
            )
            total = (await count.fetchone())["count"]
            cursor = await connection.execute(
                f"SELECT * FROM taskhub_task_index{where} "
                "ORDER BY updated_at DESC, run_id DESC LIMIT %s OFFSET %s",
                [*params, page_size, (page - 1) * page_size],
            )
            items = [TaskSummary.model_validate(dict(row)) async for row in cursor]
        return TaskPage(items=items, total=total, page=page, page_size=page_size)

    async def archive(self, run_id: str) -> TaskSummary | None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE taskhub_task_index
                   SET archived_at=COALESCE(archived_at, now()), updated_at=now()
                   WHERE run_id=%s""",
                (run_id,),
            )
        return await self.get(run_id)

    async def rebind(self, run_id: str, project_id: str) -> TaskSummary | None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE taskhub_task_index
                   SET rebound_project_id=%s, updated_at=now() WHERE run_id=%s""",
                (project_id, run_id),
            )
        return await self.get(run_id)


def _summary(requirement: str) -> str:
    compact = " ".join(requirement.split())
    return compact[:197] + "..." if len(compact) > 200 else compact


@asynccontextmanager
async def task_index_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield MemoryTaskIndex()
        return
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    async with AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
    ) as pool:
        index = PostgresTaskIndex(pool)
        await index.setup()
        yield index
