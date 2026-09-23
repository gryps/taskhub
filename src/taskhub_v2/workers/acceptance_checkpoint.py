import hashlib
import json

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import AcceptanceEvidence

CHECKPOINT_NAME = "acceptance-checkpoint-v1.json"
CHECKPOINT_EVIDENCE_IDS = {"project-acceptance", "project-acceptance-database"}


def acceptance_checkpoint_fingerprint(project, implementation) -> str:
    payload = {
        "version": 1,
        "implementation_commit": implementation.commit,
        "acceptance_commands": project.acceptance_commands,
        "acceptance_capabilities": sorted(project.acceptance_capabilities),
        "test_timeout_seconds": project.test_timeout_seconds,
        "test_environment": (
            project.test_environment.model_dump(mode="json")
            if project.test_environment
            else None
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_acceptance_checkpoint(
    artifacts: ArtifactStore, run_id: str, expected_fingerprint: str
) -> list[AcceptanceEvidence]:
    try:
        payload = json.loads(
            artifacts.path(run_id, CHECKPOINT_NAME).read_text(encoding="utf-8")
        )
        if payload.get("fingerprint") != expected_fingerprint:
            return []
        records = [AcceptanceEvidence.model_validate(item) for item in payload["evidence"]]
    except (FileNotFoundError, OSError, ValueError, TypeError, KeyError):
        return []
    if not records or any(
        item.id not in CHECKPOINT_EVIDENCE_IDS or item.status != "passed"
        for item in records
    ):
        return []
    if not any(item.id == "project-acceptance" for item in records):
        return []
    return [
        item.model_copy(
            update={
                "summary": item.summary
                + "; reused verified checkpoint for unchanged candidate"
            }
        )
        for item in records
    ]


def write_acceptance_checkpoint(
    artifacts: ArtifactStore,
    run_id: str,
    fingerprint: str,
    records: list[AcceptanceEvidence],
) -> None:
    artifacts.write_text(
        run_id,
        CHECKPOINT_NAME,
        "acceptance_checkpoint",
        json.dumps(
            {
                "version": 1,
                "fingerprint": fingerprint,
                "evidence": [item.model_dump(mode="json") for item in records],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
