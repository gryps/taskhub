from taskhub_v2.workers import contract_acceptance
from taskhub_v2.workers.windows_acceptance import verify_windows_suite


async def verify(
    project_contracts, artifacts, scheduler, run_id, project, implementation, error_type
):
    records = []
    contract = await contract_acceptance.verify_project_contract(
        project_contracts,
        artifacts,
        run_id,
        project.id,
        implementation,
    )
    if contract:
        records.append(contract)
    windows = await verify_windows_suite(
        scheduler,
        artifacts,
        run_id,
        project,
        implementation,
        error_type,
    )
    if windows:
        records.append(windows)
    return records
