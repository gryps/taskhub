#!/usr/bin/env bash
set -euo pipefail
: "${TASKHUB_TEST_POSTGRES_DSN:?Set a disposable PostgreSQL database DSN}"
# Disposable PostgreSQL clusters must be initialized with UTF-8. SQL_ASCII
# changes checkpoint identifiers to bytes and breaks interrupt recovery.
# Explicit opt-in makes missing Playwright/browser binaries fail the acceptance gate.
export TASKHUB_TEST_BROWSER=1
python -m pytest -q "$@"
