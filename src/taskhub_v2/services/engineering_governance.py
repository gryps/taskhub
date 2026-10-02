from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from taskhub_v2.domain.governance import (
    EngineeringPolicy,
    EngineeringPolicyStatus,
    EngineeringRule,
    PolicyBinding,
    PolicyException,
    PolicyExceptionStatus,
)
from taskhub_v2.domain.project_contract import ManualReviewRule, ProjectContract
from taskhub_v2.persistence.production import ProductionStore
from taskhub_v2.services.contract_gate_models import GateFinding
from taskhub_v2.services.engineering_policy_catalog import builtin_rules
from taskhub_v2.services.governance_gate import evaluate_policy

GLOBAL_POLICY_PROJECT = "__global__"
BUILTIN_POLICY_ID = "ep_global_engineering"


class EngineeringGovernanceError(RuntimeError):
    pass


class EngineeringGovernanceService:
    def __init__(self, store: ProductionStore):
        self.store = store

    async def ensure_builtin(self) -> EngineeringPolicy:
        policies = await self.list_policies()
        if policies:
            return await self.active_policy()
        rules = builtin_rules()
        policy = EngineeringPolicy(
            project_id=GLOBAL_POLICY_PROJECT,
            policy_id=BUILTIN_POLICY_ID,
            version=1,
            status=EngineeringPolicyStatus.ACTIVE,
            name="TaskHub 全局软件工程规则",
            source_reference="builtin://global-engineering-policy/v1",
            source_digest=self._rules_digest(rules),
            rules=rules,
            activated_by="system",
            activated_at=datetime.now(UTC).isoformat(),
            source_ids=["global:AGENTS.md"],
        )
        return await self.store.save(policy)

    async def list_policies(self) -> list[EngineeringPolicy]:
        records = await self.store.list(
            project_id=GLOBAL_POLICY_PROJECT, object_type="engineering_policy"
        )
        return sorted(
            (item for item in records if isinstance(item, EngineeringPolicy)),
            key=lambda item: item.version,
            reverse=True,
        )

    async def active_policy(self) -> EngineeringPolicy:
        active = [
            item
            for item in await self.list_policies()
            if item.status == EngineeringPolicyStatus.ACTIVE
        ]
        if not active:
            raise EngineeringGovernanceError("没有已生效的全局工程规则")
        return active[0]

    async def create_draft(
        self,
        *,
        name: str,
        rules: list[EngineeringRule],
        source_reference: str,
        actor: str,
    ) -> EngineeringPolicy:
        policies = await self.list_policies()
        if policies and policies[0].status == EngineeringPolicyStatus.DRAFT:
            raise EngineeringGovernanceError("已有待处理的全局规则草稿")
        previous = policies[0] if policies else None
        policy = EngineeringPolicy(
            project_id=GLOBAL_POLICY_PROJECT,
            policy_id=BUILTIN_POLICY_ID,
            version=(previous.version + 1) if previous else 1,
            previous_version=previous.version if previous else None,
            name=name,
            source_reference=source_reference,
            source_digest=self._rules_digest(rules),
            rules=rules,
            created_by=actor,
        )
        return await self.store.save(policy)

    async def activate(self, version: int, actor: str) -> EngineeringPolicy:
        policy = await self._policy(version)
        if policy.status != EngineeringPolicyStatus.DRAFT:
            raise EngineeringGovernanceError("只有规则草稿可以生效")
        for current in await self.list_policies():
            if current.status == EngineeringPolicyStatus.ACTIVE:
                await self.store.save(
                    current.model_copy(update={"status": EngineeringPolicyStatus.SUPERSEDED})
                )
        return await self.store.save(
            policy.model_copy(
                update={
                    "status": EngineeringPolicyStatus.ACTIVE,
                    "activated_by": actor,
                    "activated_at": datetime.now(UTC).isoformat(),
                }
            )
        )

    async def apply_to_contract(self, contract: ProjectContract) -> ProjectContract:
        policy = await self.active_policy()
        rules = [rule for rule in policy.rules if rule.applies(contract.profile_id)]
        required_files = [
            path for rule in rules for path in rule.required_paths if not path.endswith("/")
        ]
        docs = list(dict.fromkeys([*contract.documentation_files, *required_files]))
        file_limits = [rule.max_file_lines for rule in rules if rule.max_file_lines]
        complexity_limits = [rule.max_complexity for rule in rules if rule.max_complexity]
        modules = [
            module.model_copy(
                update={
                    "max_file_lines": min([module.max_file_lines, *file_limits]),
                    "max_function_complexity": min(
                        [module.max_function_complexity, *complexity_limits]
                    ),
                }
            )
            for module in contract.modules
        ]
        manual = list(contract.manual_review)
        known = {item.rule_id for item in manual}
        for rule in rules:
            evidence_key = rule.rule_id.replace(".", "_")
            if rule.manual_evidence and evidence_key not in known:
                manual.append(
                    ManualReviewRule(
                        rule_id=evidence_key,
                        description=rule.title,
                        required_evidence=rule.manual_evidence,
                    )
                )
        binding = PolicyBinding(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            policy_digest=policy.content_digest,
            rule_ids=[rule.rule_id for rule in rules],
            instructions=[rule.instruction for rule in rules],
            bound_at=datetime.now(UTC).isoformat(),
        )
        return contract.model_copy(
            update={
                "documentation_files": docs,
                "modules": modules,
                "manual_review": manual,
                "engineering_policy": binding,
                "source_ids": [
                    *contract.source_ids,
                    f"{policy.policy_id}:v{policy.version}:{policy.content_digest}",
                ],
            }
        )

    async def binding_status(self, contract: ProjectContract | None) -> dict:
        if contract is None or not contract.engineering_policy.policy_id:
            return {"valid": False, "current": False, "detail": "项目契约未绑定全局规则"}
        binding = contract.engineering_policy
        record = await self.store.get(
            "engineering_policy", binding.policy_id, str(binding.policy_version)
        )
        if not isinstance(record, EngineeringPolicy):
            return {"valid": False, "current": False, "detail": "绑定的全局规则快照不存在"}
        if record.content_digest != binding.policy_digest:
            return {"valid": False, "current": False, "detail": "全局规则快照摘要不一致"}
        active = await self.active_policy()
        current = active.version == record.version and active.policy_id == record.policy_id
        return {
            "valid": True,
            "current": current,
            "detail": (
                f"已绑定当前全局规则 v{record.version}"
                if current
                else f"已冻结全局规则 v{record.version}；当前为 v{active.version}，可建立合同修订"
            ),
            "policy": record,
        }

    async def gate_findings(
        self,
        repository: str | Path,
        contract: ProjectContract,
        manual_evidence: dict[str, str],
    ) -> list[GateFinding]:
        status = await self.binding_status(contract)
        if not status["valid"]:
            return [
                GateFinding(
                    gate_id="governance:binding",
                    category="governance",
                    status="failed",
                    summary=status["detail"],
                )
            ]
        policy = status["policy"]
        waived = await self._waived_rule_ids(contract.project_id, policy)
        return evaluate_policy(repository, contract, policy, waived, manual_evidence)

    async def propose_exception(
        self,
        project_id: str,
        *,
        rule_ids: list[str],
        scope: str,
        scope_ids: list[str],
        reason: str,
        risk: str,
        controls: list[str],
        recovery_condition: str,
        expires_at: datetime,
        actor: str,
    ) -> PolicyException:
        policy = await self.active_policy()
        known = {rule.rule_id for rule in policy.rules}
        if unknown := set(rule_ids) - known:
            raise EngineeringGovernanceError("未知规则：" + "、".join(sorted(unknown)))
        if expires_at <= datetime.now(UTC) or expires_at > datetime.now(UTC) + timedelta(days=365):
            raise EngineeringGovernanceError("例外到期时间必须在未来一年内")
        record = PolicyException(
            project_id=project_id,
            exception_id=f"px_{uuid4().hex}",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            rule_ids=rule_ids,
            scope=scope,
            scope_ids=scope_ids,
            reason=reason,
            risk=risk,
            controls=controls,
            recovery_condition=recovery_condition,
            expires_at=expires_at,
            created_by=actor,
        )
        return await self.store.save(record)

    async def decide_exception(
        self, project_id: str, exception_id: str, *, approve: bool, actor: str
    ) -> PolicyException:
        record = await self.store.get("policy_exception", exception_id, "1")
        if not isinstance(record, PolicyException) or record.project_id != project_id:
            raise EngineeringGovernanceError("规则例外不存在")
        if record.status != PolicyExceptionStatus.PROPOSED:
            raise EngineeringGovernanceError("规则例外已经处理")
        target = PolicyExceptionStatus.APPROVED if approve else PolicyExceptionStatus.REJECTED
        return await self.store.save(
            record.model_copy(
                update={
                    "status": target,
                    "decided_by": actor,
                    "decided_at": datetime.now(UTC).isoformat(),
                }
            )
        )

    async def project_view(self, project_id: str, contract: ProjectContract | None) -> dict:
        records = await self.store.list(project_id=project_id, object_type="policy_exception")
        return {
            "binding": await self.binding_status(contract),
            "contract_binding": contract.engineering_policy if contract else None,
            "exceptions": [item for item in records if isinstance(item, PolicyException)],
        }

    async def _policy(self, version: int) -> EngineeringPolicy:
        record = await self.store.get("engineering_policy", BUILTIN_POLICY_ID, str(version))
        if not isinstance(record, EngineeringPolicy):
            raise EngineeringGovernanceError("全局规则版本不存在")
        return record

    async def _waived_rule_ids(
        self, project_id: str, policy: EngineeringPolicy
    ) -> set[str]:
        records = await self.store.list(project_id=project_id, object_type="policy_exception")
        now = datetime.now(UTC)
        return {
            rule_id
            for item in records
            if isinstance(item, PolicyException)
            and item.status == PolicyExceptionStatus.APPROVED
            and item.scope == "project"
            and item.policy_id == policy.policy_id
            and item.policy_version == policy.version
            and item.expires_at > now
            for rule_id in item.rule_ids
        }

    @staticmethod
    def _rules_digest(rules: list[EngineeringRule]) -> str:
        encoded = json.dumps(
            [item.model_dump(mode="json") for item in rules],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return sha256(encoded).hexdigest()
