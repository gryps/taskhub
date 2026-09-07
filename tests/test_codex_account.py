import pytest

from taskhub_v2.providers.codex_account import CodexAccountProvider


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("HTTP 429: Too Many Requests", "rate_limited"),
        ('error={"code":"rate_limit_exceeded"}', "rate_limited"),
        ("HTTP 401: unauthorized", "needs_reauth"),
        ("error=insufficient_quota", "quota_exceeded"),
        (
            "HTTP 400: Invalid schema for response_format 'supervision_decision'",
            "invalid_response_schema",
        ),
        (
            "Review says rate limit handling and 429 recovery need tests",
            "account_runner_failed",
        ),
        (
            "Requirement mentions quota, usage limit, unauthorized and status code 401",
            "account_runner_failed",
        ),
    ],
)
def test_account_failure_reason_uses_explicit_error_signatures(output, expected):
    assert CodexAccountProvider._failure_reason(output) == expected
