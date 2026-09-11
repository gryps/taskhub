from __future__ import annotations

import ast
import fnmatch
import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath

from taskhub_v2.domain.project_contract import ModuleContract, ProjectContract
from taskhub_v2.services.contract_documents import validate_contract_documents
from taskhub_v2.services.contract_gate_extended import extended_findings
from taskhub_v2.services.contract_gate_models import GateFinding, ProjectGateReport


def validate_repository(
    repository: str | Path,
    contract: ProjectContract,
    *,
    strict: bool = True,
    manual_evidence: dict[str, str] | None = None,
) -> ProjectGateReport:
    root = Path(repository).resolve()
    if not root.is_dir():
        raise ValueError("project contract repository does not exist")
    files = _repository_files(root)
    findings = [
        *_structure_findings(root, files, contract, strict),
        *_dependency_findings(root, files, contract),
        *_complexity_findings(root, files, contract),
        *_migration_findings(files, contract),
        *_repository_policy_findings(root, files, contract),
        *[GateFinding(**item) for item in extended_findings(root, files, contract)],
        *[
            GateFinding(
                gate_id=f"contract-document:{index}",
                category="contract",
                status="failed",
                **item,
            )
            for index, item in enumerate(validate_contract_documents(root, contract))
        ],
        *_manual_findings(contract, manual_evidence or {}),
    ]
    status = _report_status(findings)
    return ProjectGateReport(
        project_id=contract.project_id,
        contract_id=contract.contract_id,
        contract_version=contract.version,
        repository_commit=_git_commit(root),
        status=status,
        findings=findings,
    )


def _repository_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode:
        raise ValueError("project contract gates require a Git repository")
    return sorted({line for line in result.stdout.splitlines() if line})


def _git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _structure_findings(
    root: Path, files: list[str], contract: ProjectContract, strict: bool
) -> list[GateFinding]:
    findings = []
    for directory in contract.directory_structure:
        exists = (root / directory).is_dir()
        findings.append(
            GateFinding(
                gate_id=f"directory:{directory}",
                category="structure",
                status="passed" if exists else ("failed" if strict else "warning"),
                summary=f"目录 {directory} {'存在' if exists else '缺失'}",
                path=directory,
            )
        )
    required_files = {
        *contract.artifacts.required_files,
        *contract.documentation_files,
        contract.artifacts.dockerfile,
        contract.artifacts.compose_file,
        contract.environment_example,
    }
    for required in sorted(required_files):
        exists = any(fnmatch.fnmatch(path, required) for path in files)
        findings.append(
            GateFinding(
                gate_id=f"required:{required}",
                category="artifact",
                status="passed" if exists else ("failed" if strict else "warning"),
                summary=f"必需文件 {required} {'存在' if exists else '缺失'}",
                path=required,
            )
        )
    return findings


def _module_for(path: str, modules: list[ModuleContract]) -> ModuleContract | None:
    return next(
        (module for module in modules if any(fnmatch.fnmatch(path, rule) for rule in module.paths)),
        None,
    )


def _dependency_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[GateFinding]:
    observed: set[tuple[str, str]] = set()
    findings = []
    for path in files:
        source = _module_for(path, contract.modules)
        if source is None or Path(path).suffix not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            continue
        for target_name in _imports(root / path, path, contract.modules):
            if target_name == source.name:
                continue
            observed.add((source.name, target_name))
            allowed = (
                target_name in source.may_import and target_name not in source.forbidden_imports
            )
            if not allowed:
                findings.append(
                    GateFinding(
                        gate_id=f"dependency:{source.name}:{target_name}:{path}",
                        category="architecture",
                        status="failed",
                        summary=f"模块 {source.name} 不允许依赖 {target_name}",
                        path=path,
                    )
                )
    cycle = _dependency_cycle(observed)
    if cycle:
        findings.append(
            GateFinding(
                gate_id="dependency-cycle",
                category="architecture",
                status="failed",
                summary="模块依赖存在循环：" + " → ".join(cycle),
            )
        )
    if not findings:
        findings.append(
            GateFinding(
                gate_id="dependency-direction",
                category="architecture",
                status="passed",
                summary="模块依赖方向和循环检查通过",
            )
        )
    return findings


def _imports(path: Path, relative: str, modules: list[ModuleContract]) -> set[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    names = set()
    if path.suffix == ".py":
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return set()
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        for imported in imports:
            for module in modules:
                roots = {rule.split("*")[0].strip("/").replace("/", ".") for rule in module.paths}
                if any(
                    imported == root
                    or imported.startswith(root + ".")
                    or imported.startswith(root.split(".")[-1] + ".")
                    for root in roots
                ):
                    names.add(module.name)
    else:
        for imported in re.findall(r"(?:from\s+|import\s*\()?['\"]([^'\"]+)['\"]", content):
            if not imported.startswith("."):
                continue
            target = PurePosixPath(relative).parent.joinpath(imported).as_posix()
            normalized = posixpath.normpath(target).removeprefix("./")
            module = _module_for(normalized + "/index.ts", modules) or _module_for(
                normalized, modules
            )
            if module:
                names.add(module.name)
    return names


def _dependency_cycle(edges: set[tuple[str, str]]) -> list[str]:
    graph: dict[str, set[str]] = {}
    for source, target in edges:
        graph.setdefault(source, set()).add(target)

    def visit(node: str, path: list[str]) -> list[str]:
        if node in path:
            return [*path[path.index(node) :], node]
        for target in graph.get(node, set()):
            if cycle := visit(target, [*path, node]):
                return cycle
        return []

    return next((cycle for node in graph if (cycle := visit(node, []))), [])


def _complexity_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[GateFinding]:
    failures = []
    branch_types = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.BoolOp, ast.Match)
    for relative in files:
        module = _module_for(relative, contract.modules)
        if module is None or Path(relative).suffix != ".py":
            continue
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8")
            tree = ast.parse(content)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        if len(content.splitlines()) > module.max_file_lines:
            failures.append(f"{relative} 超过 {module.max_file_lines} 行")
        for item in ast.walk(tree):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = 1 + sum(isinstance(node, branch_types) for node in ast.walk(item))
                if complexity > module.max_function_complexity:
                    failures.append(
                        f"{relative}:{item.lineno} 复杂度 {complexity} "
                        f"超过 {module.max_function_complexity}"
                    )
    return [
        GateFinding(
            gate_id=f"complexity:{index}",
            category="complexity",
            status="failed",
            summary=summary,
        )
        for index, summary in enumerate(failures)
    ] or [
        GateFinding(
            gate_id="complexity",
            category="complexity",
            status="passed",
            summary="文件行数与函数复杂度检查通过",
        )
    ]


def _migration_findings(files: list[str], contract: ProjectContract) -> list[GateFinding]:
    forward = [
        path
        for path in files
        if any(path.startswith(root.rstrip("/") + "/") for root in contract.migrations.paths)
        and not path.endswith(contract.migrations.rollback_suffix)
    ]
    sequences: dict[str, str] = {}
    failures = []
    pattern = re.compile(contract.migrations.filename_pattern)
    for path in forward:
        match = pattern.fullmatch(Path(path).name)
        if not match:
            failures.append(f"迁移文件名不符合顺序约定：{path}")
            continue
        sequence = match.groupdict().get("sequence", "")
        if sequence in sequences:
            failures.append(f"迁移序号重复：{sequences[sequence]} 与 {path}")
        sequences[sequence] = path
        if contract.migrations.rollback_required:
            rollback = str(Path(path).with_suffix("")) + contract.migrations.rollback_suffix
            if rollback not in files:
                failures.append(f"迁移缺少回滚文件：{path}")
    return [
        GateFinding(
            gate_id=f"migration:{index}",
            category="migration",
            status="failed",
            summary=summary,
        )
        for index, summary in enumerate(failures)
    ] or [
        GateFinding(
            gate_id="migration",
            category="migration",
            status="passed",
            summary="迁移顺序与回滚约定检查通过",
        )
    ]


def _repository_policy_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[GateFinding]:
    failures = []
    compiled = [re.compile(pattern) for pattern in contract.repository_policy.secret_patterns]
    for relative in files:
        if any(
            fnmatch.fnmatch(relative, rule) for rule in contract.repository_policy.forbidden_globs
        ):
            failures.append(f"禁止文件进入仓库：{relative}")
            continue
        path = root / relative
        try:
            data = path.read_bytes()
        except OSError:
            continue
        binary = b"\0" in data[:8_192]
        if binary:
            if path.suffix.lower() not in contract.repository_policy.allowed_binary_extensions:
                failures.append(f"未许可二进制文件：{relative}")
            if len(data) > contract.repository_policy.max_binary_bytes:
                failures.append(f"二进制文件超过大小限制：{relative}")
            continue
        text = data[:2_000_000].decode("utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in compiled):
            failures.append(f"疑似凭据内容：{relative}")
    return [
        GateFinding(
            gate_id=f"repository-policy:{index}",
            category="security",
            status="failed",
            summary=summary,
        )
        for index, summary in enumerate(failures)
    ] or [
        GateFinding(
            gate_id="repository-policy",
            category="security",
            status="passed",
            summary="凭据、生成物和二进制策略检查通过",
        )
    ]


def _manual_findings(contract: ProjectContract, evidence: dict[str, str]) -> list[GateFinding]:
    return [
        GateFinding(
            gate_id=f"manual:{rule.rule_id}",
            category="manual_review",
            status="passed" if evidence.get(rule.rule_id, "").strip() else "manual",
            summary=(
                f"人工证据已提供：{rule.description}"
                if evidence.get(rule.rule_id, "").strip()
                else f"需要人工评审：{rule.description}"
            ),
            detail=evidence.get(rule.rule_id, "") or rule.required_evidence,
        )
        for rule in contract.manual_review
    ]


def _report_status(findings: list[GateFinding]) -> str:
    if any(item.status == "failed" for item in findings):
        return "failed"
    if any(item.status == "manual" for item in findings):
        return "manual_review"
    return "passed"
