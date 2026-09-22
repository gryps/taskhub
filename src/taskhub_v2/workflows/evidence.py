import json


def delivery_evidence(state: dict) -> str:
    """Serialize delivery evidence with explicit ownership of executed commands."""
    return json.dumps(
        {
            "evidence_provenance": {
                "implementation.evidence": (
                    "Coding-worker report; use this field when judging actions performed "
                    "by the coding model."
                ),
                "implementation.tests": (
                    "Commands executed by a TaskHub verification worker after coding; these "
                    "are not actions performed by the coding model."
                ),
                "acceptance.evidence": (
                    "Commands and artifacts produced by TaskHub acceptance workers after "
                    "implementation; judge their results as independent verification and do "
                    "not attribute their execution to the coding model."
                ),
            },
            "implementation": state.get("implementation") or {},
            "acceptance": state.get("acceptance") or {},
        },
        ensure_ascii=False,
    )
