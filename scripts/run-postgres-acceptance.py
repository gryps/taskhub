#!/usr/bin/env python3
"""Run PostgreSQL acceptance in an isolated schema of the configured database."""

import os
import subprocess
import sys


def main() -> int:
    dsn = os.environ.get("TASKHUB_POSTGRES_DSN", "")
    if not dsn:
        print("TASKHUB_POSTGRES_DSN is not configured", file=sys.stderr)
        return 2
    # pytest fixtures allocate and clean a unique schema for each test.
    environment = dict(os.environ)
    environment["TASKHUB_TEST_POSTGRES_DSN"] = dsn
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
