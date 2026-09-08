from taskhub_v2.domain.models import TestExecution as ExecutionRecord
from taskhub_v2.workers.test_diagnostics import failed_test_diagnostics


def test_failed_test_diagnostics_preserves_command_and_failure_summary():
    tests = [
        ExecutionRecord(command=["npm", "run", "lint"], exit_code=0, output_tail="ok"),
        ExecutionRecord(
            command=["npm", "run", "api:test"],
            exit_code=1,
            output_tail=(
                "E AssertionError: database is locked\n"
                "FAILED tests/test_shop.py::test_isolation\n"
            ),
        ),
    ]

    detail, records = failed_test_diagnostics(tests)

    assert "Command failed (exit 1): npm run api:test" in detail
    assert "FAILED tests/test_shop.py::test_isolation" in detail
    assert records == [
        {
            "command": ["npm", "run", "api:test"],
            "exit_code": 1,
            "output": (
                "E AssertionError: database is locked\n"
                "FAILED tests/test_shop.py::test_isolation\n"
            ),
        }
    ]
