from taskhub_v2.domain.models import TestExecution

MAX_FAILURE_DETAIL = 16_000


def classify_test_failure(tests: list[TestExecution]) -> str:
    """Separate infrastructure timeouts from failures that merit a code revision."""
    return "tests_timeout" if any(test.exit_code == 124 for test in tests) else "tests_failed"


def failed_test_diagnostics(tests: list[TestExecution]) -> tuple[str, list[dict]]:
    failures = [test for test in tests if test.exit_code]
    records = [
        {
            "command": test.command,
            "exit_code": test.exit_code,
            "output": test.output_tail[-MAX_FAILURE_DETAIL:],
        }
        for test in failures
    ]
    sections = []
    for record in records:
        command = " ".join(record["command"])
        sections.append(
            f"Command failed (exit {record['exit_code']}): {command}\n{record['output']}"
        )
    return "\n\n".join(sections)[-MAX_FAILURE_DETAIL:], records
