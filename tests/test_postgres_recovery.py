import asyncio
import os

import pytest

from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ApprovalRequest, RunStatus, StartRunRequest
from taskhub_v2.persistence.checkpoints import checkpoint_store
from taskhub_v2.services.runs import RunService
from taskhub_v2.workflows import build_main_graph
from tests.fakes import RecordingProvider, RecordingWorker


@pytest.mark.skipif(
    not os.getenv("TASKHUB_TEST_POSTGRES_DSN"),
    reason="TASKHUB_TEST_POSTGRES_DSN is not configured",
)
def test_postgres_checkpoint_survives_runtime_recreation():
    async def scenario():
        settings = Settings(
            checkpointer="postgres",
            postgres_dsn=os.environ["TASKHUB_TEST_POSTGRES_DSN"],
        )

        async with checkpoint_store(settings) as first_store:
            first_worker = RecordingWorker()
            first_graph = build_main_graph(RecordingProvider(), first_worker, first_store)
            waiting = await RunService(first_graph).start(
                StartRunRequest(project_id="recovery", requirement="Survive service restart")
            )
            assert first_worker.calls == 0

        async with checkpoint_store(settings) as second_store:
            second_worker = RecordingWorker()
            second_graph = build_main_graph(RecordingProvider(), second_worker, second_store)
            service = RunService(second_graph)

            restored = await service.get(waiting.run_id)
            assert restored.status == RunStatus.WAITING

            completed = await service.approve(
                waiting.run_id, ApprovalRequest(decision="approve")
            )
            assert completed.status == RunStatus.COMPLETED
            assert second_worker.calls == 1

    asyncio.run(scenario())
