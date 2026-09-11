from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path

from taskhub_v2.domain.project_contract import ProjectContract


def extended_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[dict[str, str]]:
    return [
        *_interface_findings(root, files, contract),
        *_data_access_findings(root, files, contract),
        *_delivery_findings(root, files, contract),
        *_binary_license_findings(root, files, contract),
    ]


def _interface_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[dict[str, str]]:
    findings = []
    for interface in contract.interfaces:
        source = root / interface.source
        generated = [
            path
            for path in files
            if any(fnmatch.fnmatch(path, pattern) for pattern in interface.generated_paths)
        ]
        digest_path = root / interface.digest_file
        if not source.is_file():
            findings.append(_failed("api", f"接口源文件缺失：{interface.source}", interface.source))
            continue
        if not generated:
            findings.append(_failed("api", f"生成客户端缺失：{interface.name}", interface.source))
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        actual = digest_path.read_text(encoding="utf-8").strip() if digest_path.is_file() else ""
        if actual != expected:
            findings.append(
                _failed(
                    "api", f"接口与生成客户端摘要不一致：{interface.name}", interface.digest_file
                )
            )
    return findings or [_passed("api", "API 合同与生成客户端一致性检查通过")]


def _data_access_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[dict[str, str]]:
    findings = []
    pattern = re.compile(r"\b(?:SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.I)
    for relative in files:
        module = next(
            (
                item
                for item in contract.modules
                if any(fnmatch.fnmatch(relative, rule) for rule in item.paths)
            ),
            None,
        )
        if module is None or module.allow_data_access:
            continue
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if pattern.search(text):
            findings.append(
                _failed(
                    "data-access",
                    f"模块 {module.name} 不允许直接访问数据库：{relative}",
                    relative,
                )
            )
    return findings or [_passed("data-access", "跨模块数据访问限制检查通过")]


def _delivery_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[dict[str, str]]:
    existing = [
        path
        for path in (contract.artifacts.dockerfile, contract.artifacts.compose_file)
        if path in files
    ]
    if not existing:
        return []
    health = contract.artifacts.health_path
    if any(
        health in (root / path).read_text(encoding="utf-8", errors="ignore") for path in existing
    ):
        return [_passed("delivery-health", "容器交付文件声明了健康检查约定")]
    return [
        _failed(
            "delivery-health",
            f"Dockerfile/Compose 未引用健康路径 {health}",
            contract.artifacts.compose_file,
        )
    ]


def _binary_license_findings(
    root: Path, files: list[str], contract: ProjectContract
) -> list[dict[str, str]]:
    extensions = set(contract.repository_policy.allowed_binary_extensions)
    binaries = []
    for relative in files:
        path = root / relative
        try:
            if path.suffix.lower() in extensions and b"\0" in path.read_bytes()[:8_192]:
                binaries.append(relative)
        except OSError:
            continue
    if not binaries:
        return [_passed("binary-license", "仓库没有需要单独授权记录的二进制资产")]
    license_file = contract.repository_policy.binary_license_file
    if license_file not in files:
        return [_failed("binary-license", "二进制资产缺少许可证清单", license_file)]
    return [_passed("binary-license", f"{len(binaries)} 个二进制资产具有许可证清单")]


def _failed(gate_id: str, summary: str, path: str) -> dict[str, str]:
    return {
        "gate_id": gate_id,
        "category": "architecture",
        "status": "failed",
        "summary": summary,
        "path": path,
    }


def _passed(gate_id: str, summary: str) -> dict[str, str]:
    return {
        "gate_id": gate_id,
        "category": "architecture",
        "status": "passed",
        "summary": summary,
    }
