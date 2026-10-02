import subprocess
from pathlib import Path

from taskhub_v2.domain.governance import EngineeringPolicy, EngineeringRule
from taskhub_v2.domain.project_contract import ProjectContract
from taskhub_v2.services.contract_gate_models import GateFinding
from taskhub_v2.services.source_metrics import python_function_metrics

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
    failures = _required_path_findings(root, rule)
    failures.extend(_required_command_findings(contract, rule))
    if rule.max_file_lines or rule.max_function_lines or rule.max_complexity:
        failures.extend(_source_size_findings(root, files, rule))
    return _rule_gate_findings(rule, evidence, failures)


def _required_path_findings(root, rule):
    failures = []
    for required in rule.required_paths:
        is_directory = required.endswith("/")
        target = root / required.rstrip("/")
        if not (target.is_dir() if is_directory else target.is_file()):
            failures.append((f"缺少全局规则要求的路径：{required}", required))
    return failures


def _required_command_findings(contract, rule):
    failures = []
    for group in rule.required_command_groups:
        if not getattr(contract.commands, group):
            failures.append((f"缺少全局规则要求的质量命令组：{group}", ""))
    return failures


def _rule_gate_findings(rule, evidence, failures):
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
        if rule.max_file_lines and len(lines) > rule.max_file_lines:
            failures.append((f"{relative} 超过全局上限 {rule.max_file_lines} 行", relative))
        if path.suffix == ".py" and (rule.max_function_lines or rule.max_complexity):
            failures.extend(_python_function_findings(relative, lines, rule))
    return failures


def _python_function_findings(relative, lines, rule):
    try:
        metrics = python_function_metrics("\n".join(lines))
    except SyntaxError:
        return []
    failures = []
    for metric in metrics:
        if rule.max_function_lines and metric.lines > rule.max_function_lines:
            failures.append(
                (
                    f"{relative}:{metric.line} 函数超过全局上限 {rule.max_function_lines} 行",
                    relative,
                )
            )
        if rule.max_complexity and metric.complexity > rule.max_complexity:
            failures.append(
                (
                    f"{relative}:{metric.line} 函数圈复杂度 {metric.complexity} "
                    f"超过全局上限 {rule.max_complexity}",
                    relative,
                )
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
