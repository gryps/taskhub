from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver

from taskhub_v2.config import Settings


@asynccontextmanager
async def checkpoint_store(settings: Settings):
    if settings.checkpointer == "memory":
        yield InMemorySaver()
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
        await saver.setup()
        yield saver
