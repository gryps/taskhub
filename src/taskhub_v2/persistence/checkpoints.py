from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver

from taskhub_v2.config import Settings


@asynccontextmanager
async def checkpoint_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield InMemorySaver()
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=1,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
