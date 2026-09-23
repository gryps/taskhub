from pathlib import Path

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import AcceptanceEvidence, WindowsTestSuiteDefinition


async def verify_windows_suite(
    scheduler,
    artifacts: ArtifactStore,
    run_id: str,
    project,
    implementation,
    error_type,
) -> AcceptanceEvidence | None:
    suite: WindowsTestSuiteDefinition | None = project.windows_test_suite
    if suite is None:
        return None
    if not implementation.workspace or not implementation.commit:
        raise error_type("Windows 实机测试需要已提交的 Git 工作区")
    browser_suite = "playwright" in suite.required_capabilities

    scheduled = await scheduler.run(
        f"{run_id}-windows",
        f"{run_id}-windows",
        suite.commands,
        project.test_timeout_seconds,
        implementation.workspace.path,
        workload="browser_acceptance" if browser_suite else "acceptance",
        required_capabilities_override=suite.required_capabilities,
        eligible_node_ids=suite.node_ids or None,
        git_commit=implementation.commit,
        artifact_paths=suite.artifact_paths,
        execution_environment=(
            project.test_environment.execution_environment()
            if project.test_environment
            else {}
        ),
    )
    actual_commit = scheduled.metadata.get("git_commit")
    if actual_commit and actual_commit != implementation.commit:
        raise error_type("Windows 实机测试证据与候选提交不一致")

    downloaded = scheduled.metadata.pop("downloaded_artifacts", [])
    stored = [
        artifacts.write_bytes(
            run_id,
            item["path"].replace("/", "-").replace("\\", "-"),
            _artifact_kind(item["path"]),
            item["content"],
            expected_sha256=item["sha256"],
            metadata={**scheduled.metadata, "original_path": item["path"]},
        )
        for item in downloaded
    ]
    found = {item.metadata.get("original_path") for item in stored}
    missing = [
        requested
        for requested in suite.artifact_paths
        if not any(_matches_artifact(path, requested) for path in found if path)
    ]
    if missing:
        raise error_type("Windows 实机测试缺少产物：" + "、".join(missing))

    failed = [test for test in scheduled.tests if test.exit_code]
    evidence = AcceptanceEvidence(
        id="project-windows-test-suite",
        kind="browser" if browser_suite else "test",
        status="failed" if failed else "passed",
        source=scheduled.node_id,
        summary=(
            f"{len(scheduled.tests) - len(failed)}/{len(scheduled.tests)} 条 Windows "
            f"实机测试通过；候选提交 {implementation.commit}"
        ),
        tests=scheduled.tests,
        artifacts=stored,
    )
    if failed:
        raise error_type(failed[0].output_tail or "Windows 实机测试失败")
    return evidence


def _matches_artifact(actual: str, requested: str) -> bool:
    actual_parts = Path(actual.replace("\\", "/")).parts
    requested_parts = Path(requested.replace("\\", "/")).parts
    return (
        actual_parts == requested_parts
        or actual_parts[: len(requested_parts)] == requested_parts
    )


def _artifact_kind(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith("trace.zip"):
        return "browser_trace"
    if lowered.endswith(".xml"):
        return "junit_report"
    if lowered.endswith((".png", ".jpg", ".jpeg")):
        return "screenshot"
    if lowered.endswith((".webm", ".mp4")):
        return "browser_video"
    return "windows_test_artifact"
