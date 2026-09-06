"""Run against a disposable PostgreSQL database via TASKHUB_TEST_POSTGRES_DSN."""
import asyncio
import os

import pytest

from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ApprovalRequest, ResumeRequest, StartRunRequest
from taskhub_v2.persistence.checkpoints import checkpoint_store
from taskhub_v2.persistence.task_index import task_index_store
from taskhub_v2.services.runs import RunService
from taskhub_v2.workflows import build_main_graph
from tests.fakes import RecordingProvider, RecordingWorker
from tests.test_workflow import RecoveringPublisher


@pytest.mark.skipif(not os.getenv('TASKHUB_TEST_POSTGRES_DSN'), reason='PostgreSQL DSN not configured')
def test_postgres_index_history_repair_and_resume_after_restart():
    async def scenario():
        settings = Settings(checkpointer='postgres',
                            postgres_dsn=os.environ['TASKHUB_TEST_POSTGRES_DSN'])
        provider, worker, publisher = RecordingProvider(), RecordingWorker(), RecoveringPublisher()
        async with checkpoint_store(settings) as saver, task_index_store(settings) as index:
            service = RunService(build_main_graph(provider, worker, saver, publisher),
                                 task_index=index)
            runs = [await service.start(StartRunRequest(
                project_id='pg-history', requirement=f'Persistent feature {n}', production_line=line
            )) for n, line in enumerate(('A', 'B', 'A'))]
            target = runs[0].run_id
            await service.approve(target, ApprovalRequest(decision='approve'))
            blocked = await service.resume(target, ResumeRequest(decision='approve'))
            assert blocked.blocking_reason['detail'] == 'temporary publication failure'
            created_at = (await index.get(target)).created_at
            # Simulate a stale row and an absent row after an interrupted index write.
            await index.upsert(dict(run_id=target, project_id='pg-history', requirement='stale',
                                    current_stage='intake', status='running'))
            await index.connection.execute('DELETE FROM taskhub_task_index WHERE run_id=%s',
                                           (runs[1].run_id,))
            await index.connection.commit()

        async with checkpoint_store(settings) as saver, task_index_store(settings) as index:
            service = RunService(build_main_graph(provider, worker, saver, publisher),
                                 task_index=index)
            await service.backfill(saver)
            await service.backfill(saver)
            page = await service.list(project_id='pg-history', page_size=200)
            assert {r.run_id for r in runs} <= {r.run_id for r in page.items}
            restored = await service.get(target)
            assert restored.created_at == created_at
            assert restored.status == 'blocked'
            assert restored.pending_action['choices'] == ['retry', 'cancel']
            assert (await index.get(runs[1].run_id)).production_line == 'B'
            filtered = await service.list(project_id='pg-history', production_line='A',
                                          status='blocked', stage='merge_blocked')
            assert target in {item.run_id for item in filtered.items}
            completed = await service.resume(target, ResumeRequest(decision='retry'))
            assert completed.status == 'completed'
            assert worker.calls == provider.review_calls == provider.supervisor_calls == 1
            assert provider.plan_calls == 3 and publisher.calls == 2

        async with task_index_store(settings) as index:
            assert (await index.get(target)).status == 'completed'
            assert (await index.get(target)).blocking_reason is None
    asyncio.run(scenario())
