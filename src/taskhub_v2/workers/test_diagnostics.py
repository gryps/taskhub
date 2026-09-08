from taskhub_v2.domain.models import TestExecution

MAX_FAILURE_DETAIL = 16_000


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
