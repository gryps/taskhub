import ast
from pathlib import Path

from taskhub_v2.services.source_metrics import python_function_metrics

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "taskhub_v2"

REQUIRED_BASELINE_PATHS = (
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/MODULES.md",
    "docs/PRODUCT.md",
    "docs/DESIGN.md",
    "docs/FRONTEND_ARCHITECTURE.md",
    "docs/DECISIONS",
)

LEGACY_FRONTEND_FILE_LINES = {
    "src/taskhub_v2/api/static/app.js": 1424,
    "src/taskhub_v2/api/static/index.html": 731,
    "src/taskhub_v2/api/static/resource-center.js": 1291,
    "src/taskhub_v2/api/static/styles.css": 1472,
}

LEGACY_FUNCTION_LINES = {
    ("src/taskhub_v2/config.py", "get_settings"): 102,
    ("src/taskhub_v2/workflows/main_graph.py", "build_main_graph"): 343,
    ("src/taskhub_v2/workflows/implementation.py", "build_implementation_graph"): 103,
    ("src/taskhub_v2/node_agent/app.py", "create_node_app"): 261,
    ("src/taskhub_v2/node_agent/app.py", "create_node_app.execute"): 101,
    ("src/taskhub_v2/deployment/runner.py", "deploy"): 104,
    ("src/taskhub_v2/persistence/production.py", "_validate_replacement"): 118,
    ("src/taskhub_v2/api/app.py", "create_app"): 250,
    ("src/taskhub_v2/api/app.py", "create_app.lifespan"): 175,
    ("src/taskhub_v2/workers/git_coder.py", "GitCodingWorker.execute"): 168,
    ("src/taskhub_v2/workers/acceptance.py", "ProjectAcceptanceGateway.verify"): 256,
    ("src/taskhub_v2/workers/publisher.py", "GitPublisher.publish"): 125,
    ("src/taskhub_v2/services/dag_scheduler.py", "PersistentDagScheduler.execute"): 177,
    ("src/taskhub_v2/services/topologies.py", "TopologyService._findings"): 122,
    ("src/taskhub_v2/services/capability_catalog.py", "builtin_capability_packs"): 120,
    ("src/taskhub_v2/services/containers.py", "ContainerManager.create"): 126,
    ("src/taskhub_v2/services/onboarding.py", "onboarding_status"): 123,
    ("src/taskhub_v2/services/revisions.py", "RevisionService.apply"): 114,
    ("src/taskhub_v2/services/remote_nodes.py", "RemoteNodeService._provision_locked"): 106,
    ("src/taskhub_v2/services/project_preflight.py", "ProjectPreflightService.run"): 272,
}

LEGACY_FUNCTION_COMPLEXITY = {
    ("src/taskhub_v2/security/auth.py", "AuthService.read_session"): 16,
    ("src/taskhub_v2/security/auth.py", "AuthService.upsert_user"): 16,
    ("src/taskhub_v2/projects/registry.py", "ProjectRegistry._validate_remote_url"): 16,
    ("src/taskhub_v2/providers/openai.py", "OpenAIResponsesProvider._http_failure"): 17,
    ("src/taskhub_v2/workflows/implementation.py", "build_implementation_graph.implement"): 16,
    ("src/taskhub_v2/workflows/browser_acceptance.py", "request_browser_acceptance"): 17,
    ("src/taskhub_v2/workflows/manual_handoff.py", "handle_revision_limit"): 29,
    ("src/taskhub_v2/node_agent/coding.py", "coding_available"): 16,
    ("src/taskhub_v2/browser/preview.py", "PreviewManager.start"): 17,
    ("src/taskhub_v2/browser/reports.py", "validate_junit"): 30,
    ("src/taskhub_v2/execution/scheduler.py", "NodeScheduler.run"): 19,
    ("src/taskhub_v2/execution/scheduler.py", "NodeScheduler._acquire"): 36,
    ("src/taskhub_v2/persistence/production.py", "_validate_replacement"): 43,
    ("src/taskhub_v2/api/app.py", "create_app.lifespan"): 16,
    ("src/taskhub_v2/api/routes.py", "start_run"): 19,
    ("src/taskhub_v2/workers/git_coder.py", "GitCodingWorker.execute"): 29,
    ("src/taskhub_v2/workers/acceptance.py", "ProjectAcceptanceGateway.verify"): 52,
    ("src/taskhub_v2/workers/dag_executor.py", "WorkerDagExecutor.finalize"): 24,
    ("src/taskhub_v2/workers/windows_acceptance.py", "verify_windows_suite"): 25,
    ("src/taskhub_v2/workers/publisher.py", "GitPublisher.publish"): 24,
    ("src/taskhub_v2/domain/configuration.py", "ModelServicesUpdate.validate_card_routes"): 20,
    (
        "src/taskhub_v2/services/configuration.py",
        "ManagedConfigurationService.update_model_services",
    ): 21,
    ("src/taskhub_v2/services/dag_scheduler.py", "PersistentDagScheduler.execute"): 36,
    ("src/taskhub_v2/services/dag_plan.py", "DagPlanService.compile"): 24,
    ("src/taskhub_v2/services/dag_plan.py", "DagPlanService._tasks"): 22,
    ("src/taskhub_v2/services/dag_readiness.py", "waiting_reasons"): 18,
    ("src/taskhub_v2/services/topologies.py", "TopologyService._findings"): 55,
    ("src/taskhub_v2/services/contract_gates.py", "_imports"): 20,
    ("src/taskhub_v2/services/node_model_config.py", "write_node_model_configuration"): 20,
    ("src/taskhub_v2/services/containers.py", "ContainerManager.create"): 18,
    ("src/taskhub_v2/services/onboarding.py", "model_ready"): 17,
    ("src/taskhub_v2/services/onboarding.py", "onboarding_status"): 28,
    ("src/taskhub_v2/services/revisions.py", "RevisionService.propose"): 23,
    ("src/taskhub_v2/services/revisions.py", "RevisionService.apply"): 24,
    ("src/taskhub_v2/services/run_actions.py", "build_resume_command"): 16,
    ("src/taskhub_v2/services/dag_analysis.py", "analyze_execution"): 45,
    (
        "src/taskhub_v2/services/engineering_governance.py",
        "EngineeringGovernanceService.apply_to_contract",
    ): 17,
    ("src/taskhub_v2/services/project_contracts.py", "ProjectContractService.gate"): 21,
    ("src/taskhub_v2/services/providers.py", "ProviderCatalog._card_status"): 23,
    ("src/taskhub_v2/services/capabilities.py", "CapabilityService.create_lock"): 16,
    ("src/taskhub_v2/services/exceptions.py", "ExceptionCenterService._view"): 16,
    ("src/taskhub_v2/services/device_auth.py", "CodexDeviceAuthService._run"): 17,
    ("src/taskhub_v2/services/project_preflight.py", "ProjectPreflightService.run"): 71,
    ("src/taskhub_v2/services/runs.py", "RunService.get"): 16,
    (
        "src/taskhub_v2/services/remote_node_lifecycle.py",
        "RemoteNodeLifecycleMixin._reconcile_node",
    ): 16,
    ("src/taskhub_v2/services/evidence.py", "EvidenceCenterService._project"): 39,
    (
        "src/taskhub_v2/services/capability_compatibility.py",
        "compatibility_report",
    ): 18,
}


def test_required_engineering_baseline_exists():
    missing = [path for path in REQUIRED_BASELINE_PATHS if not (ROOT / path).exists()]
    assert not missing, f"Missing engineering baseline paths: {missing}"


def test_python_modules_remain_below_size_limit():
    oversized = []
    for path in (ROOT / "src").rglob("*.py"):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > 400:
            oversized.append(f"{path.relative_to(ROOT)}: {line_count}")
    assert not oversized, "Oversized Python modules:\n" + "\n".join(oversized)


def test_frontend_files_obey_limit_or_exact_no_growth_baseline():
    roots = (ROOT / "src" / "taskhub_v2" / "api" / "static",)
    findings = []
    seen_legacy = set()
    for source_root in roots:
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {
                ".css",
                ".html",
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
            }:
                continue
            relative = str(path.relative_to(ROOT))
            count = len(path.read_text(encoding="utf-8").splitlines())
            baseline = LEGACY_FRONTEND_FILE_LINES.get(relative)
            if baseline is None and count > 400:
                findings.append(f"new oversized frontend file {relative}: {count} > 400")
            elif baseline is not None:
                seen_legacy.add(relative)
                if count != baseline:
                    direction = "grew" if count > baseline else "shrank; lower the baseline"
                    findings.append(f"{relative}: {count}, baseline {baseline} ({direction})")
    stale = set(LEGACY_FRONTEND_FILE_LINES) - seen_legacy
    findings.extend(f"remove stale frontend baseline: {path}" for path in sorted(stale))
    assert not findings, "Frontend size governance findings:\n" + "\n".join(findings)


def test_python_functions_obey_length_and_complexity_no_growth_baselines():
    findings = []
    seen_lines = set()
    seen_complexity = set()
    for path in (ROOT / "src").rglob("*.py"):
        relative = str(path.relative_to(ROOT))
        for metric in python_function_metrics(path.read_text(encoding="utf-8")):
            key = (relative, metric.name)
            line_baseline = LEGACY_FUNCTION_LINES.get(key)
            complexity_baseline = LEGACY_FUNCTION_COMPLEXITY.get(key)
            if line_baseline is None and metric.lines > 100:
                findings.append(
                    f"new long function {relative}:{metric.line} {metric.name}: {metric.lines}"
                )
            elif line_baseline is not None:
                seen_lines.add(key)
                if metric.lines != line_baseline:
                    direction = (
                        "grew" if metric.lines > line_baseline else "shrank; lower the baseline"
                    )
                    findings.append(
                        f"{relative} {metric.name}: {metric.lines}, "
                        f"line baseline {line_baseline} ({direction})"
                    )
            if complexity_baseline is None and metric.complexity > 15:
                findings.append(
                    f"new complex function {relative}:{metric.line} "
                    f"{metric.name}: {metric.complexity}"
                )
            elif complexity_baseline is not None:
                seen_complexity.add(key)
                if metric.complexity != complexity_baseline:
                    direction = (
                        "grew"
                        if metric.complexity > complexity_baseline
                        else "shrunk; lower the baseline"
                    )
                    findings.append(
                        f"{relative} {metric.name}: {metric.complexity}, complexity baseline "
                        f"{complexity_baseline} ({direction})"
                    )
    findings.extend(
        f"remove stale line baseline: {key}"
        for key in sorted(set(LEGACY_FUNCTION_LINES) - seen_lines)
    )
    findings.extend(
        f"remove stale complexity baseline: {key}"
        for key in sorted(set(LEGACY_FUNCTION_COMPLEXITY) - seen_complexity)
    )
    assert not findings, "Python function governance findings:\n" + "\n".join(findings)


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT / "src").with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _internal_dependencies(path: Path, known: set[str]) -> set[str]:
    current = _module_name(path)
    current_package = current.split(".")[:-1]
    dependencies = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        candidates = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = current_package[: len(current_package) - node.level + 1]
                base = ".".join([*package, *(node.module or "").split(".")]).rstrip(".")
            else:
                base = node.module or ""
            candidates.append(base)
            candidates.extend(f"{base}.{alias.name}" for alias in node.names if base)
        for candidate in candidates:
            parts = candidate.split(".")
            while parts:
                name = ".".join(parts)
                if name in known and name != current:
                    dependencies.add(name)
                    break
                parts.pop()
    return dependencies


def test_taskhub_python_import_graph_has_no_cycles():
    paths = list(SRC.rglob("*.py"))
    modules = {_module_name(path): path for path in paths}
    graph = {name: _internal_dependencies(path, set(modules)) for name, path in modules.items()}
    visiting = []
    visited = set()
    cycles = []

    def visit(module):
        if module in visiting:
            start = visiting.index(module)
            cycles.append(" -> ".join([*visiting[start:], module]))
            return
        if module in visited:
            return
        visiting.append(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.pop()
        visited.add(module)

    for module in graph:
        visit(module)
    assert not cycles, "Python import cycles:\n" + "\n".join(sorted(set(cycles)))


def test_platform_core_has_no_business_project_coupling():
    forbidden = ("douyin", "抖店", "listing-workbench")
    violations = []
    for path in (ROOT / "src").rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".html", ".css"}:
            continue
        content = path.read_text(encoding="utf-8").lower()
        if any(word in content for word in forbidden):
            violations.append(str(path.relative_to(ROOT)))
    assert not violations, f"Business coupling found in: {violations}"
