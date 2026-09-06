#!/usr/bin/env python3
"""Run PostgreSQL acceptance in an isolated schema of the configured database."""

import os
import subprocess
import sys

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def main() -> int:
    dsn = os.environ.get("TASKHUB_POSTGRES_DSN", "")
    if not dsn:
        print("TASKHUB_POSTGRES_DSN is not configured", file=sys.stderr)
        return 2
    schema = "taskhub_acceptance"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    parameters = conninfo_to_dict(dsn)
    parameters["options"] = f"-c search_path={schema}"
    environment = dict(os.environ)
    environment["TASKHUB_TEST_POSTGRES_DSN"] = make_conninfo(**parameters)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_task_center_postgres.py",
            "tests/test_postgres_recovery.py",
        ],
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
