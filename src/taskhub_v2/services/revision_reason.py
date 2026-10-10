MAX_CHANGE_REQUEST_REASON_LENGTH = 4_000
TRUNCATION_MARKER = "\n\n...[earlier recovery detail truncated]...\n\n"


def bounded_change_request_reason(reason: str) -> str:
    """Fit recovery diagnostics to the durable ChangeRequest contract.

    Keep both the failure context at the beginning and the concrete command tail. The latter
    normally contains the most actionable package, test, or compiler error.
    """
    if len(reason) <= MAX_CHANGE_REQUEST_REASON_LENGTH:
        return reason
    tail_length = 2_700
    head_length = MAX_CHANGE_REQUEST_REASON_LENGTH - len(TRUNCATION_MARKER) - tail_length
    return f"{reason[:head_length]}{TRUNCATION_MARKER}{reason[-tail_length:]}"
