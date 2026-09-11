from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from taskhub_v2.domain.change_request import ChangeRequest, ChangeRequestStatus
from taskhub_v2.domain.production import (
    ProductDecision,
    ProductDecisionStatus,
    ProductSpec,
    ProductSpecStatus,
    Requirement,
    RequirementAttachment,
    RequirementStatus,
    RequirementSupplement,
)
from taskhub_v2.persistence.production import ProductionStore
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.services.productization_intake import (
    functional_items,
    missing_information,
    non_functional_items,
    risk_items,
    scope_items,
    subject,
)


class ProductizationNotFoundError(LookupError):
    pass


class ProductizationConflictError(RuntimeError):
    pass


class ProductizationService:
    def __init__(self, store: ProductionStore, provider: ModelProvider):
        self.store = store
        self.provider = provider

    async def submit_requirement(
        self,
        *,
        project_id: str,
        original_text: str,
        attachments: list[RequirementAttachment],
        actor: str,
    ) -> dict[str, Any]:
        normalized = "\n".join(
            line.strip() for line in original_text.strip().splitlines() if line.strip()
        )
        result = await self.provider.create_plan(normalized)
        suffix = uuid4().hex
        requirement_id = f"req_{suffix}"
        spec_id = f"ps_{suffix}"
        questions = missing_information(normalized)
        decision_id = f"decision_{uuid4().hex}" if questions else None
        requirement = Requirement(
            project_id=project_id,
            requirement_id=requirement_id,
            status=(
                RequirementStatus.CLARIFICATION_REQUIRED
                if questions
                else RequirementStatus.PRODUCTIZED
            ),
            original_text=original_text,
            attachments=attachments,
            created_by=actor,
            source_ids=[requirement_id],
            productization={
                "provider": result.provider,
                "model": result.model,
                "normalized_text": normalized,
                "planning_hints": list(result.content.steps),
            },
        )
        plan = result.content
        spec = ProductSpec(
            project_id=project_id,
            spec_id=spec_id,
            version=1,
            created_by=actor,
            source_ids=[requirement_id],
            source_requirement_ids=[requirement_id],
            source_requirement_snapshots={requirement_id: original_text},
            pending_decision_ids=[decision_id] if decision_id else [],
            summary=plan.summary,
            goals=[subject(normalized)],
            in_scope=scope_items(normalized),
            functional_requirements=functional_items(normalized),
            non_functional_requirements=non_functional_items(normalized),
            acceptance_criteria=list(plan.acceptance),
            risks=risk_items(normalized),
            assumptions=["ProductSpec 草稿由需求产品化生成，批准前需由项目负责人复核。"],
        )
        requirement = await self.store.save(requirement)
        spec = await self.store.save(spec)
        decision = None
        if decision_id:
            decision = await self.store.save(
                ProductDecision(
                    project_id=project_id,
                    decision_id=decision_id,
                    requirement_id=requirement_id,
                    spec_id=spec_id,
                    spec_version=1,
                    title="产品规格待决策事项",
                    questions=questions,
                    created_by=actor,
                    source_ids=[requirement_id, spec_id],
                )
            )
        return {"requirement": requirement, "product_spec": spec, "decision": decision}

    async def append_supplement(
        self, project_id: str, requirement_id: str, text: str, actor: str
    ) -> Requirement:
        requirement = await self._requirement(project_id, requirement_id)
        supplement = RequirementSupplement(
            supplement_id=f"supplement_{uuid4().hex}", text=text, created_by=actor
        )
        updated = self._replace(
            requirement,
            supplements=[*requirement.supplements, supplement],
        )
        return await self.store.save(updated)

    async def list_requirements(self, project_id: str) -> list[Requirement]:
        records = await self.store.list(project_id=project_id, object_type="requirement")
        return sorted(
            (item for item in records if isinstance(item, Requirement)),
            key=lambda item: item.submitted_at,
            reverse=True,
        )

    async def list_specs(self, project_id: str) -> list[ProductSpec]:
        records = await self.store.list(project_id=project_id, object_type="product_spec")
        return sorted(
            (item for item in records if isinstance(item, ProductSpec)),
            key=lambda item: (item.created_at, item.version),
            reverse=True,
        )

    async def current_spec(self, project_id: str) -> ProductSpec | None:
        specs = await self.list_specs(project_id)
        return specs[0] if specs else None

    async def detail(self, project_id: str, spec_id: str, version: int) -> dict[str, Any]:
        spec = await self._spec(project_id, spec_id, version)
        requirements = [
            await self._requirement(project_id, requirement_id)
            for requirement_id in spec.source_requirement_ids
        ]
        decisions = []
        records = await self.store.list(project_id=project_id, object_type="product_decision")
        for item in records:
            if (
                isinstance(item, ProductDecision)
                and item.spec_id == spec_id
                and item.spec_version == version
            ):
                decisions.append(item)
        return {"product_spec": spec, "requirements": requirements, "decisions": decisions}

    async def update_draft(
        self, project_id: str, spec_id: str, version: int, changes: dict[str, Any]
    ) -> ProductSpec:
        spec = await self._spec(project_id, spec_id, version)
        if spec.status not in {ProductSpecStatus.DRAFT, ProductSpecStatus.IN_REVIEW}:
            raise ProductizationConflictError("approved or superseded ProductSpec is immutable")
        allowed = {
            "summary",
            "goals",
            "personas",
            "in_scope",
            "out_of_scope",
            "functional_requirements",
            "non_functional_requirements",
            "modules",
            "interfaces",
            "data_entities",
            "security_requirements",
            "delivery_requirements",
            "acceptance_criteria",
            "risks",
            "assumptions",
            "capability_pack_lock",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ProductizationConflictError(
                f"fields cannot be edited: {', '.join(sorted(invalid))}"
            )
        return await self.store.save(self._replace(spec, **changes))

    async def resolve_decision(
        self, project_id: str, decision_id: str, answers: dict[str, str], actor: str
    ) -> ProductSpec:
        decision = await self._decision(project_id, decision_id)
        if decision.status != ProductDecisionStatus.PENDING:
            raise ProductizationConflictError("product decision is already resolved")
        required = {question.key for question in decision.questions}
        if required != set(answers) or any(not value.strip() for value in answers.values()):
            raise ProductizationConflictError("all product decision questions require an answer")
        resolved = self._replace(
            decision,
            status=ProductDecisionStatus.RESOLVED,
            answers={key: value.strip() for key, value in answers.items()},
            resolved_by=actor,
            resolved_at=datetime.now(UTC),
        )
        await self.store.save(resolved)
        spec = await self._spec(project_id, decision.spec_id, decision.spec_version)
        changes: dict[str, Any] = {
            "pending_decision_ids": [
                item for item in spec.pending_decision_ids if item != decision_id
            ],
            "decision_resolutions": {**spec.decision_resolutions, **resolved.answers},
        }
        if answer := resolved.answers.get("target_users"):
            changes["personas"] = [*spec.personas, answer]
        if answer := resolved.answers.get("acceptance_definition"):
            changes["acceptance_criteria"] = [*spec.acceptance_criteria, answer]
        if answer := resolved.answers.get("delivery_target"):
            changes["delivery_requirements"] = [*spec.delivery_requirements, answer]
        spec = await self.store.save(self._replace(spec, **changes))
        requirement = await self._requirement(project_id, decision.requirement_id)
        await self.store.save(
            self._replace(
                requirement,
                status=RequirementStatus.PRODUCTIZED,
                productization={**requirement.productization, "decision_answers": resolved.answers},
            )
        )
        return spec

    async def submit_review(self, project_id: str, spec_id: str, version: int) -> ProductSpec:
        spec = await self._spec(project_id, spec_id, version)
        if spec.status != ProductSpecStatus.DRAFT:
            raise ProductizationConflictError("only a draft ProductSpec can enter review")
        if spec.pending_decision_ids:
            raise ProductizationConflictError("resolve product decisions before review")
        if not spec.goals or not spec.acceptance_criteria:
            raise ProductizationConflictError("goals and acceptance criteria are required")
        return await self.store.save(self._replace(spec, status=ProductSpecStatus.IN_REVIEW))

    async def approve(self, project_id: str, spec_id: str, version: int) -> ProductSpec:
        spec = await self._spec(project_id, spec_id, version)
        if spec.status != ProductSpecStatus.IN_REVIEW:
            raise ProductizationConflictError("only an in-review ProductSpec can be approved")
        for current in await self.list_specs(project_id):
            if current.status == ProductSpecStatus.APPROVED:
                await self.store.save(self._replace(current, status=ProductSpecStatus.SUPERSEDED))
        return await self.store.save(self._replace(spec, status=ProductSpecStatus.APPROVED))

    async def create_revision(
        self, project_id: str, spec_id: str, version: int, reason: str, actor: str
    ) -> ProductSpec:
        previous = await self._spec(project_id, spec_id, version)
        if previous.status != ProductSpecStatus.APPROVED:
            raise ProductizationConflictError("only an approved ProductSpec can be revised")
        change_request_id = f"cr_{uuid4().hex}"
        await self.store.save(
            ChangeRequest(
                project_id=project_id,
                change_request_id=change_request_id,
                status=ChangeRequestStatus.APPROVED,
                reason=reason,
                source_event="product_spec.revision_requested",
                created_by=actor,
                source_ids=[spec_id],
            )
        )
        values = previous.model_dump()
        snapshots = {}
        for requirement_id in previous.source_requirement_ids:
            requirement = await self._requirement(project_id, requirement_id)
            snapshots[requirement_id] = self._requirement_source(requirement)
        values.update(
            version=version + 1,
            status=ProductSpecStatus.DRAFT,
            previous_version=version,
            change_request_id=change_request_id,
            created_by=actor,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            content_digest="",
            source_ids=[*previous.source_ids, change_request_id],
            source_requirement_snapshots=snapshots,
        )
        return await self.store.save(ProductSpec.model_validate(values))

    async def diff(
        self, project_id: str, spec_id: str, from_version: int, to_version: int
    ) -> list[dict[str, Any]]:
        before = await self._spec(project_id, spec_id, from_version)
        after = await self._spec(project_id, spec_id, to_version)
        excluded = {"created_at", "updated_at", "content_digest", "created_by"}
        left = before.model_dump(mode="json", exclude=excluded)
        right = after.model_dump(mode="json", exclude=excluded)
        return [
            {"field": key, "before": left.get(key), "after": right.get(key)}
            for key in sorted(left.keys() | right.keys())
            if left.get(key) != right.get(key)
        ]

    async def execution_source(
        self, project_id: str, spec_id: str, version: int
    ) -> tuple[ProductSpec, str]:
        spec = await self._spec(project_id, spec_id, version)
        if spec.status != ProductSpecStatus.APPROVED:
            raise ProductizationConflictError("ProductSpec must be approved before implementation")
        texts = [
            spec.source_requirement_snapshots[requirement_id]
            for requirement_id in spec.source_requirement_ids
            if requirement_id in spec.source_requirement_snapshots
        ]
        if not texts:
            for requirement_id in spec.source_requirement_ids:
                texts.append(
                    self._requirement_source(await self._requirement(project_id, requirement_id))
                )
        return spec, "\n\n".join(texts)

    async def _requirement(self, project_id: str, requirement_id: str) -> Requirement:
        record = await self.store.get("requirement", requirement_id, "1")
        if not isinstance(record, Requirement) or record.project_id != project_id:
            raise ProductizationNotFoundError("requirement not found")
        return record

    async def _spec(self, project_id: str, spec_id: str, version: int) -> ProductSpec:
        record = await self.store.get("product_spec", spec_id, str(version))
        if not isinstance(record, ProductSpec) or record.project_id != project_id:
            raise ProductizationNotFoundError("ProductSpec not found")
        return record

    async def _decision(self, project_id: str, decision_id: str) -> ProductDecision:
        record = await self.store.get("product_decision", decision_id, "1")
        if not isinstance(record, ProductDecision) or record.project_id != project_id:
            raise ProductizationNotFoundError("product decision not found")
        return record

    @staticmethod
    def _replace(record, **changes):
        return type(record).model_validate({**record.model_dump(), **changes})

    @staticmethod
    def _requirement_source(requirement: Requirement) -> str:
        return "\n\n".join(
            [requirement.original_text, *(item.text for item in requirement.supplements)]
        )
