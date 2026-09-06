"""PostgreSQL tests own both their index and checkpoint tables, even on a shared DB."""

import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def postgres_namespace():
    dsn = os.getenv("TASKHUB_TEST_POSTGRES_DSN")
    if not dsn:
        if os.getenv("TASKHUB_TEST_BROWSER") == "1":
            pytest.fail("TASKHUB_TEST_POSTGRES_DSN is required for browser acceptance")
        pytest.skip("TASKHUB_TEST_POSTGRES_DSN is not configured")

    @contextmanager
    def namespace():
        schema = "taskhub_test_" + uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            try:
                # No public fallback: migrations and backfill must never see shared tables.
                yield make_conninfo(dsn, options=f"-csearch_path={schema}"), schema
            finally:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )
                assert connection.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
                ).fetchone() is None

    return namespace


@pytest.fixture
def postgres_dsn(postgres_namespace):
    with postgres_namespace() as (dsn, _):
        yield dsn
