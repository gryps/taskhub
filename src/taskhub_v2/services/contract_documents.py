from pathlib import Path

import yaml

from taskhub_v2.domain.project_contract import ProjectContract


def render_contract_documents(contract: ProjectContract) -> dict[str, str]:
    project = {
        "schema_version": contract.schema_version,
        "contract_id": contract.contract_id,
        "version": contract.version,
        "profile": contract.profile_id,
        "languages": contract.languages,
        "frameworks": contract.frameworks,
        "directory_structure": contract.directory_structure,
        "commands": contract.commands.model_dump(mode="json"),
        "artifacts": contract.artifacts.model_dump(mode="json"),
        "documentation_files": contract.documentation_files,
        "environment_example": contract.environment_example,
    }
    architecture = {
        "schema_version": contract.schema_version,
        "contract_id": contract.contract_id,
        "version": contract.version,
        "modules": [item.model_dump(mode="json") for item in contract.modules],
        "interfaces": [item.model_dump(mode="json") for item in contract.interfaces],
        "migrations": contract.migrations.model_dump(mode="json"),
        "repository_policy": contract.repository_policy.model_dump(mode="json"),
        "manual_review": [item.model_dump(mode="json") for item in contract.manual_review],
    }
    acceptance = {
        "contract_schema_version": contract.schema_version,
        "contract_id": contract.contract_id,
        "contract_version": contract.version,
        "quality_commands": contract.commands.acceptance,
        "expected_artifacts": contract.artifacts.required_artifacts,
        "health_path": contract.artifacts.health_path,
    }
    return {
        ".taskhub/project.yaml": _dump(project),
        ".taskhub/architecture.yaml": _dump(architecture),
        ".taskhub/acceptance.yaml": _dump(acceptance),
    }


def validate_contract_documents(root: Path, contract: ProjectContract) -> list[dict[str, str]]:
    findings = []
    for relative, expected_text in render_contract_documents(contract).items():
        path = root / relative
        if not path.is_file():
            continue
        try:
            actual = yaml.safe_load(path.read_text(encoding="utf-8"))
            expected = yaml.safe_load(expected_text)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            findings.append(
                {"path": relative, "summary": f"合同文档无法解析：{relative}", "detail": str(error)}
            )
            continue
        if not isinstance(actual, dict):
            findings.append(
                {"path": relative, "summary": f"合同文档必须是 YAML 映射：{relative}", "detail": ""}
            )
            continue
        mismatched = [key for key, value in expected.items() if actual.get(key) != value]
        if mismatched:
            findings.append(
                {
                    "path": relative,
                    "summary": f"合同文档与批准版本不一致：{relative}",
                    "detail": "字段：" + ", ".join(mismatched),
                }
            )
    return findings


def _dump(payload: dict) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
