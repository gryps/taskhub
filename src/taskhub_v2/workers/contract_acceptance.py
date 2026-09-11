from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import AcceptanceEvidence
from taskhub_v2.services.contract_gate_models import ProjectContractGateError


async def verify_project_contract(
    service,
    artifacts: ArtifactStore,
    run_id: str,
    project_id: str,
    implementation,
) -> AcceptanceEvidence | None:
    if not service or not implementation.workspace:
        return None
    report = await service.gate(
        project_id,
        workspace=implementation.workspace.path,
        execute_commands=True,
        strict=True,
        job_id=f"{run_id}-contract-gates",
    )
    artifact = artifacts.write_text(
        run_id,
        "project-contract-gates.json",
        "architecture_compliance",
        report.model_dump_json(indent=2),
    )
    if report.status != "passed":
        raise ProjectContractGateError(report)
    return AcceptanceEvidence(
        id="project-contract-gates",
        kind="other",
        status="passed",
        source="project-contract",
        summary=(
            f"ProjectContract {report.contract_id} v{report.contract_version} "
            f"passed {len(report.findings)} checks"
        ),
        artifacts=[artifact],
    )
