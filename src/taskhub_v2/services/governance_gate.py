import ast
import subprocess
from pathlib import Path

from taskhub_v2.domain.governance import EngineeringPolicy, EngineeringRule
from taskhub_v2.domain.project_contract import ProjectContract
from taskhub_v2.services.contract_gate_models import GateFinding

SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}


def evaluate_policy(
    repository: str | Path,
    contract: ProjectContract,
    policy: EngineeringPolicy,
    waived_rule_ids: set[str],
    manual_evidence: dict[str, str],
) -> list[GateFinding]:
    root = Path(repository).resolve()
    files = _repository_files(root)
    rules = [rule for rule in policy.rules if rule.rule_id in contract.engineering_policy.rule_ids]
    findings: list[GateFinding] = []
    for rule in rules:
        rule_findings = _evaluate_rule(root, files, contract, rule, manual_evidence)
        for finding in rule_findings:
            if finding.status == "failed" and rule.rule_id in waived_rule_ids:
                finding.status = "warning"
                finding.detail = (finding.detail + "；已由有效的规则例外临时放行").strip("；")
        findings.extend(rule_findings)
    return findings


def _evaluate_rule(root, files, contract, rule: EngineeringRule, evidence) -> list[GateFinding]:
    failures = []
    for required in rule.required_paths:
        is_directory = required.endswith("/")
        target = root / required.rstrip("/")
        if not (target.is_dir() if is_directory else target.is_file()):
            failures.append((f"缺少全局规则要求的路径：{required}", required))
    for group in rule.required_command_groups:
        if not getattr(contract.commands, group):
            failures.append((f"缺少全局规则要求的质量命令组：{group}", ""))
    if rule.max_file_lines:
        failures.extend(_source_size_findings(root, files, rule))
    evidence_key = rule.rule_id.replace(".", "_")
    if rule.manual_evidence and not evidence.get(evidence_key, "").strip():
        return [
            GateFinding(
                gate_id=f"governance:{rule.rule_id}:manual",
                category=rule.category,
                status="manual" if not failures else "failed",
                summary=(failures[0][0] if failures else f"需要治理证据：{rule.title}"),
                detail=rule.manual_evidence,
            )
        ]
    if failures:
        return [
            GateFinding(
                gate_id=f"governance:{rule.rule_id}:{index}",
                category=rule.category,
                status="failed" if rule.blocking else "warning",
                summary=summary,
                path=path,
            )
            for index, (summary, path) in enumerate(failures)
        ]
    return [
        GateFinding(
            gate_id=f"governance:{rule.rule_id}",
            category=rule.category,
            status="passed",
            summary=f"全局规则通过：{rule.title}",
        )
    ]


def _source_size_findings(root, files, rule):
    failures = []
    for relative in files:
        path = root / relative
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if len(lines) > rule.max_file_lines:
            failures.append((f"{relative} 超过全局上限 {rule.max_file_lines} 行", relative))
        if rule.max_function_lines and path.suffix == ".py":
            failures.extend(_python_function_findings(relative, lines, rule.max_function_lines))
    return failures


def _python_function_findings(relative, lines, maximum):
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return []
    failures = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
            length = node.end_lineno - node.lineno + 1
            if length > maximum:
                failures.append(
                    (f"{relative}:{node.lineno} 函数超过全局上限 {maximum} 行", relative)
                )
    return failures


def _repository_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode:
        raise ValueError("全局规则门禁要求项目为 Git 仓库")
    return sorted({line for line in result.stdout.splitlines() if line})
