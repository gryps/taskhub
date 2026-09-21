"""Run against a disposable PostgreSQL database via TASKHUB_TEST_POSTGRES_DSN."""
import asyncio
import json

import psycopg
import pytest

from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ApprovalRequest, ResumeRequest, StartRunRequest
from taskhub_v2.persistence.checkpoints import checkpoint_store
from taskhub_v2.persistence.task_index import task_index_store
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.services.runs import RunConflictError, RunService
from taskhub_v2.workflows import build_main_graph
from tests.fakes import RecordingProvider, RecordingWorker
from tests.test_workflow import RecoveringPublisher


def test_postgres_upsert_keeps_archive_lookup_separate_from_production_line(postgres_dsn):
    async def scenario():
        settings = Settings(checkpointer='postgres', postgres_dsn=postgres_dsn)
        async with task_index_store(settings) as index:
            values = dict(run_id='archived-run', project_id='original',
                          requirement='Archived task', current_stage='planning', status='running')
            await index.upsert(values, 'line-A')
            archived = await index.archive(values['run_id'])
            async with index.pool.connection() as connection:
                await connection.execute('DELETE FROM taskhub_task_index WHERE run_id=%s',
                                         (values['run_id'],))
            restored = await index.upsert(values, 'line-A')
            assert restored.archived_at == archived.archived_at
            assert restored.production_line == 'line-A'
            assert (await index.list()).total == 0
            assert (await index.list(include_archived=True)).total == 1

            # A line name matching another task ID must not inherit its tombstone.
            active_values = dict(values, run_id='active-run')
            active = await index.upsert(active_values, values['run_id'])
            assert active.archived_at is None
            assert active.production_line == values['run_id']
            # Omitting the line on an identical update must preserve the row unchanged.
            unchanged = await index.upsert(active_values)
            assert unchanged == active
            changed = await index.upsert(dict(active_values, status='waiting'))
            assert changed.production_line == active.production_line
            assert changed.created_at == active.created_at
            assert changed.status == 'waiting'
            assert [item.run_id for item in (await index.list()).items] == ['active-run']

    asyncio.run(scenario())


def test_postgres_index_history_repair_and_resume_after_restart(postgres_dsn):
    async def scenario():
        settings = Settings(checkpointer='postgres',
                            postgres_dsn=postgres_dsn)
        provider, worker, publisher = RecordingProvider(), RecordingWorker(), RecoveringPublisher()
        async with checkpoint_store(settings) as saver, task_index_store(settings) as index:
            service = RunService(build_main_graph(provider, worker, saver, publisher),
                                 task_index=index)
            runs = [await service.start(StartRunRequest(
                project_id='pg-history', requirement=f'Persistent feature {n}', production_line=line
            )) for n, line in enumerate(('A', 'B', 'A'))]
            target = runs[0].run_id
            blocked = runs[0]
            assert blocked.blocking_reason['detail'] == 'temporary publication failure'
            created_at = (await index.get(target)).created_at
            # Simulate a stale row and an absent row after an interrupted index write.
            await index.upsert(dict(run_id=target, project_id='pg-history', requirement='stale',
                                    current_stage='intake', status='running'))
            async with index.pool.connection() as connection:
                await connection.execute(
                    'DELETE FROM taskhub_task_index WHERE run_id=%s', (runs[1].run_id,)
                )

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
            attention = await index.attention(project_id='pg-history')
            assert attention.total == 1
            assert attention.items[0].run_id == target
            completed = await service.resume(target, ResumeRequest(decision='retry'))
            assert completed.status == 'completed'
            assert (await index.attention(project_id='pg-history')).total == 0
            assert worker.calls == provider.review_calls == provider.supervisor_calls == 3
            assert provider.plan_calls == 3 and publisher.calls == 4

        async with task_index_store(settings) as index:
            assert (await index.get(target)).status == 'completed'
            assert (await index.get(target)).blocking_reason is None
    asyncio.run(scenario())


def test_postgres_archive_and_rebind_survive_checkpoint_backfill(postgres_dsn, tmp_path):
    async def scenario():
        path = tmp_path / 'projects.json'
        path.write_text(json.dumps({'projects': [
            {'id': project, 'repository': str(tmp_path / project)}
            for project in ('original', 'replacement')
        ]}))
        projects = ProjectRegistry(str(path))
        settings = Settings(checkpointer='postgres', postgres_dsn=postgres_dsn)
        provider, worker = RecordingProvider(), RecordingWorker()
        async with checkpoint_store(settings) as saver, task_index_store(settings) as index:
            service = RunService(build_main_graph(provider, worker, saver),
                                 projects, index)
            archived_run = await service.start(StartRunRequest(
                project_id='original', requirement='Archive before restart'))
            publisher = RecoveringPublisher()
            service = RunService(build_main_graph(provider, worker, saver, publisher),
                                 projects, index)
            rebound_run = await service.start(StartRunRequest(
                project_id='original', requirement='Rebind before restart'))
            archived = await service.archive(archived_run.run_id)
            await service.rebind(rebound_run.run_id, 'replacement')
            # Delete the archived index row: its independent tombstone must prevent revival.
            async with index.pool.connection() as connection:
                await connection.execute('DELETE FROM taskhub_task_index WHERE run_id=%s',
                                         (archived_run.run_id,))
            await index.upsert(dict(run_id=rebound_run.run_id, project_id='original',
                                    requirement='stale', current_stage='intake', status='running'))
            assert await index.get(archived_run.run_id) is None

        path.write_text(json.dumps({'projects': [
            {'id': 'replacement', 'repository': str(tmp_path / 'replacement')}
        ]}))
        # Recreate pools, graph, service and index, exactly as on process restart.
        async with checkpoint_store(settings) as saver, task_index_store(settings) as index:
            graph = build_main_graph(provider, worker, saver, publisher)
            service = RunService(graph, projects, index)
            await service.backfill(saver)
            await service.backfill(saver)
            assert [item.run_id for item in (await service.list()).items] == [rebound_run.run_id]
            assert [item.run_id for item in
                    (await service.list(project_id='replacement')).items] == [rebound_run.run_id]
            assert (await service.list(project_id='original')).total == 0
            page = await service.list(include_archived=True)
            assert page.total == 2
            restored = await service.get(archived_run.run_id, sync=True)
            assert restored.archived_at == archived.archived_at
            assert restored.stage == 'completed' and restored.project_missing
            assert (await index.get(archived_run.run_id)).requirement_summary != 'stale'
            assert (await saver.aget(service._config(archived_run.run_id))) is not None
            with pytest.raises(RunConflictError, match='archived'):
                await service.approve(archived_run.run_id, ApprovalRequest(decision='approve'))
            rebound = await service.get(rebound_run.run_id)
            assert rebound.project_id == 'replacement' and not rebound.project_missing
            assert rebound.stage == 'merge_blocked'
            # Binding is index metadata; backfill/rebind did not rewrite historical checkpoints.
            snapshot = await graph.aget_state(service._config(rebound_run.run_id))
            assert snapshot.values['project_id'] == 'original'
            completed = await service.resume(rebound_run.run_id, ResumeRequest(decision='retry'))
            assert completed.status == 'completed'
            assert completed.publication.project_id == 'replacement'
            assert worker.calls == provider.review_calls == provider.supervisor_calls == 2
            assert publisher.calls == 2

        async with task_index_store(settings) as index:
            assert (await index.list()).total == 1
            assert (await index.get(archived_run.run_id)).archived_at == archived.archived_at
            assert (await index.get(rebound_run.run_id)).rebound_project_id == 'replacement'

    asyncio.run(scenario())


def test_postgres_namespaces_isolate_index_checkpoints_and_cleanup(postgres_namespace):
    async def write_and_check(first_dsn, second_dsn):
        first = Settings(checkpointer='postgres', postgres_dsn=first_dsn)
        second = Settings(checkpointer='postgres', postgres_dsn=second_dsn)
        async with checkpoint_store(first) as saver, task_index_store(first) as index:
            service = RunService(build_main_graph(RecordingProvider(), RecordingWorker(), saver),
                                 task_index=index)
            run = await service.start(StartRunRequest(
                project_id='isolation', requirement='Visible only in owning namespace'))
        async with checkpoint_store(second) as saver, task_index_store(second) as index:
            service = RunService(build_main_graph(RecordingProvider(), RecordingWorker(), saver),
                                 task_index=index)
            await service.backfill(saver)
            assert (await index.list(include_archived=True)).total == 0
            assert await saver.aget(service._config(run.run_id)) is None
            assert [item async for item in saver.alist(None)] == []
        async with checkpoint_store(first) as saver, task_index_store(first) as index:
            assert (await index.list()).total == 1
            assert await saver.aget(service._config(run.run_id)) is not None

    with postgres_namespace() as (outer_dsn, outer_schema):
        # The outer namespace must remain open for the cleanup assertions below.
        with pytest.raises(RuntimeError, match='simulated test failure'):  # noqa: SIM117
            with postgres_namespace() as (inner_dsn, inner_schema):
                assert outer_schema != inner_schema
                asyncio.run(write_and_check(outer_dsn, inner_dsn))
                raise RuntimeError('simulated test failure')
        with psycopg.connect(outer_dsn) as connection:
            assert connection.execute(
                'SELECT 1 FROM pg_namespace WHERE nspname=%s', (inner_schema,)
            ).fetchone() is None
            tables = {row[0] for row in connection.execute(
                'SELECT tablename FROM pg_tables WHERE schemaname=%s', (outer_schema,)
            )}
            assert {'taskhub_task_index', 'checkpoints', 'checkpoint_writes',
                    'checkpoint_blobs', 'checkpoint_migrations', 'taskhub_task_archive'} <= tables
