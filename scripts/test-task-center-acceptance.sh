#!/usr/bin/env bash
set -euo pipefail
: "${TASKHUB_TEST_POSTGRES_DSN:?Set a disposable PostgreSQL database DSN}"
# Explicit opt-in makes missing Playwright/browser binaries fail the acceptance gate.
export TASKHUB_TEST_BROWSER=1
python -m pytest -q "$@"
