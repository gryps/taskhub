from __future__ import annotations

from datetime import UTC, datetime

from taskhub_v2.domain.capability import (
    CapabilityPack,
    CapabilityPackLock,
    DesignRecommendation,
    ProjectDesignContract,
)
from taskhub_v2.domain.production import ProductSpec, ProductSpecStatus
from taskhub_v2.domain.project_contract import ProjectContract, ProjectContractStatus
from taskhub_v2.services.capability_catalog import (
    PLATFORM_PROJECT,
    RECOMMENDATION_COMBINATIONS,
    builtin_capability_packs,
)
from taskhub_v2.services.capability_compatibility import compatibility_report
from taskhub_v2.services.capability_security import (
    manifest_digest,
    semantic_version,
    validate_import_manifest,
)


class CapabilityNotFoundError(LookupError):
    pass


class CapabilityConflictError(RuntimeError):
    pass


class CapabilityService:
    def __init__(self, store, projects):
        self.store = store
        self.projects = projects

    async def ensure_builtins(self) -> None:
        for pack in builtin_capability_packs():
            if not await self.store.get("capability_pack", pack.pack_id, pack.version):
                await self.store.save(
                    pack.model_copy(update={"manifest_digest": manifest_digest(pack)})
                )

    async def inventory(self) -> list[CapabilityPack]:
        records = await self.store.list(project_id=PLATFORM_PROJECT, object_type="capability_pack")
        return sorted(
            (item for item in records if isinstance(item, CapabilityPack)),
            key=lambda item: (item.pack_type, item.name, semantic_version(item.version)),
        )

    async def import_manifest(self, manifest: dict, actor: str) -> CapabilityPack:
        values = dict(manifest)
        values.update(
            project_id=PLATFORM_PROJECT,
            status="draft",
            source="admin-import",
            trusted_by="",
            trusted_at=None,
            created_by=actor,
        )
        try:
            validate_import_manifest(values)
        except ValueError as error:
            raise CapabilityConflictError(str(error)) from error
        pack = CapabilityPack.model_validate(values)
        if await self.store.get("capability_pack", pack.pack_id, pack.version):
            raise CapabilityConflictError("capability pack version already exists")
        pack = pack.model_copy(update={"manifest_digest": manifest_digest(pack)})
        return await self.store.save(pack)

    async def trust(
        self, pack_id: str, version: str, actor: str, *, executable_confirmed: bool = False
    ) -> CapabilityPack:
        pack = await self._pack(pack_id, version)
        if pack.status != "draft":
            raise CapabilityConflictError("only a draft capability pack can be trusted")
        if pack.contains_executable and not executable_confirmed:
            raise CapabilityConflictError(
                "executable capability pack requires explicit confirmation"
            )
        return await self.store.save(
            pack.model_copy(
                update={"status": "trusted", "trusted_by": actor, "trusted_at": datetime.now(UTC)}
            )
        )

    async def set_enabled(
        self, pack_id: str, version: str, enabled: bool, reason: str = ""
    ) -> CapabilityPack:
        pack = await self._pack(pack_id, version)
        target = "trusted" if enabled else "disabled"
        if pack.status not in {"trusted", "disabled"}:
            raise CapabilityConflictError("only trusted or disabled packs can change availability")
        return await self.store.save(
            pack.model_copy(update={"status": target, "disabled_reason": "" if enabled else reason})
        )

    async def recommendations(
        self, project_id: str, spec_id: str, spec_version: int
    ) -> list[DesignRecommendation]:
        contract = await self._active_project_contract(project_id)
        await self._spec(project_id, spec_id, spec_version)
        inventory = {
            item.pack_id: item for item in await self.inventory() if item.status == "trusted"
        }
        shared = ["pack_components_native_web", "pack_brand_neutral"]
        recommendations = []
        for index, (name, summary, style, layout) in enumerate(RECOMMENDATION_COMBINATIONS, 1):
            packs = [inventory.get(item) for item in [style, *shared, layout]]
            if any(item is None for item in packs):
                continue
            report = compatibility_report(contract, packs)
            if not report["compatible"]:
                continue
            tokens = packs[0].content.get("design_tokens", {})
            recommendations.append(
                DesignRecommendation(
                    recommendation_id=f"design-{index}",
                    name=name,
                    summary=summary,
                    pack_refs=[item.ref for item in packs],
                    compatibility=report,
                    previews=self._previews(name, tokens),
                )
            )
        return recommendations[:3]

    async def create_lock(
        self,
        project_id: str,
        spec_id: str,
        spec_version: int,
        pack_refs: list[str],
        actor: str,
    ) -> CapabilityPackLock:
        self.projects.get(project_id)
        spec = await self._spec(project_id, spec_id, spec_version)
        if spec.status not in {ProductSpecStatus.DRAFT, ProductSpecStatus.IN_REVIEW}:
            raise CapabilityConflictError(
                "create a ProductSpec revision before changing its capability lock"
            )
        contract = await self._active_project_contract(project_id)
        packs = [await self._ref(ref) for ref in pack_refs]
        unavailable = [item.ref for item in packs if item.status != "trusted"]
        if unavailable:
            raise CapabilityConflictError(
                "packs are not trusted and enabled: " + ", ".join(unavailable)
            )
        report = compatibility_report(contract, packs)
        if not report["compatible"]:
            raise CapabilityConflictError(
                "incompatible capability combination: " + "; ".join(report["reasons"])
            )
        existing = await self._locks(project_id)
        latest = max(existing, key=lambda item: item.version) if existing else None
        active = next((item for item in existing if item.status == "active"), None)
        version = latest.version + 1 if latest else 1
        old_refs = set(active.pack_refs) if active else set()
        changed = sorted(old_refs ^ set(pack_refs)) if active else []
        migrations = [f"评估并迁移 {ref} 影响的组件、布局和视觉回归基线" for ref in changed]
        return await self.store.save(
            CapabilityPackLock(
                project_id=project_id,
                lock_id=f"lock_{project_id}",
                version=version,
                spec_id=spec_id,
                spec_version=spec_version,
                pack_refs=pack_refs,
                compatibility_report=report,
                previous_version=latest.version if latest else None,
                migration_tasks=migrations,
                created_by=actor,
                source_ids=[spec_id, *(active.pack_refs if active else [])],
            )
        )

    async def activate_lock(
        self, project_id: str, version: int, actor: str
    ) -> tuple[CapabilityPackLock, ProjectDesignContract]:
        lock = await self._lock(project_id, version)
        if lock.status == "active":
            return lock, await self._design_contract(project_id, lock.lock_id, lock.version)
        if lock.status != "draft":
            raise CapabilityConflictError("only a draft capability lock can be activated")
        locks = await self._locks(project_id)
        if lock.version != max(item.version for item in locks):
            raise CapabilityConflictError("a stale capability lock draft cannot be activated")
        spec = await self._spec(project_id, lock.spec_id, lock.spec_version)
        if spec.status not in {ProductSpecStatus.DRAFT, ProductSpecStatus.IN_REVIEW}:
            raise CapabilityConflictError(
                "select and activate capability packs before approving ProductSpec"
            )
        packs = [await self._ref(ref) for ref in lock.pack_refs]
        unavailable = [item.ref for item in packs if item.status != "trusted"]
        if unavailable:
            raise CapabilityConflictError(
                "packs are no longer trusted and enabled: " + ", ".join(unavailable)
            )
        project_contract = await self._active_project_contract(project_id)
        report = compatibility_report(project_contract, packs)
        if not report["compatible"]:
            raise CapabilityConflictError(
                "capability lock is no longer compatible: " + "; ".join(report["reasons"])
            )
        design = self._compile_contract(project_id, lock, packs, project_contract, actor)
        current = next(
            (item for item in await self._locks(project_id) if item.status == "active"), None
        )
        if current:
            old_design = await self._design_contract(project_id, current.lock_id, current.version)
            await self.store.save(old_design.model_copy(update={"status": "superseded"}))
            await self.store.save(current.model_copy(update={"status": "superseded"}))
        active = await self.store.save(
            lock.model_copy(
                update={
                    "status": "active",
                    "activated_by": actor,
                    "activated_at": datetime.now(UTC),
                }
            )
        )
        design = await self.store.save(design)
        await self.store.save(spec.model_copy(update={"capability_pack_lock": active.pack_refs}))
        return active, design

    async def current(self, project_id: str) -> dict:
        self.projects.get(project_id)
        active = next(
            (item for item in await self._locks(project_id) if item.status == "active"), None
        )
        drafts = sorted(
            (item for item in await self._locks(project_id) if item.status == "draft"),
            key=lambda item: item.version,
            reverse=True,
        )
        design = (
            await self._design_contract(project_id, active.lock_id, active.version)
            if active
            else None
        )
        return {"active_lock": active, "draft_locks": drafts, "design_contract": design}

    async def design_for_refs(
        self,
        project_id: str,
        pack_refs: list[str],
        spec_id: str = "",
        spec_version: int = 0,
    ) -> tuple[CapabilityPackLock, ProjectDesignContract] | None:
        for lock in await self._locks(project_id):
            if (
                lock.status == "active"
                and lock.pack_refs == pack_refs
                and (not spec_id or lock.spec_id == spec_id)
                and (not spec_version or lock.spec_version == spec_version)
            ):
                return lock, await self._design_contract(project_id, lock.lock_id, lock.version)
        return None

    async def validate_spec_lock(self, spec: ProductSpec) -> None:
        records = await self.store.list(project_id=spec.project_id, object_type="project_contract")
        contracts = [
            item
            for item in records
            if isinstance(item, ProjectContract) and item.status == ProjectContractStatus.ACTIVE
        ]
        if not contracts:
            return
        contract = max(contracts, key=lambda item: item.version)
        if contract.profile_id not in {"fullstack-web", "frontend-spa"}:
            return
        if not spec.capability_pack_lock:
            raise CapabilityConflictError(
                "frontend ProductSpec requires an active exact-version capability lock"
            )
        if not await self.design_for_refs(
            spec.project_id, spec.capability_pack_lock, spec.spec_id, spec.version
        ):
            raise CapabilityConflictError(
                "ProductSpec capability references do not match an active design contract"
            )

    async def _pack(self, pack_id: str, version: str) -> CapabilityPack:
        record = await self.store.get("capability_pack", pack_id, version)
        if not isinstance(record, CapabilityPack):
            raise CapabilityNotFoundError(f"{pack_id}@{version}")
        return record

    async def _ref(self, ref: str) -> CapabilityPack:
        if "@" not in ref or ref.endswith("@latest"):
            raise CapabilityConflictError("capability references require exact semantic versions")
        return await self._pack(*ref.rsplit("@", 1))

    async def _spec(self, project_id: str, spec_id: str, version: int) -> ProductSpec:
        record = await self.store.get("product_spec", spec_id, str(version))
        if not isinstance(record, ProductSpec) or record.project_id != project_id:
            raise CapabilityNotFoundError(spec_id)
        return record

    async def _active_project_contract(self, project_id: str) -> ProjectContract:
        records = await self.store.list(project_id=project_id, object_type="project_contract")
        active = [
            item
            for item in records
            if isinstance(item, ProjectContract) and item.status == ProjectContractStatus.ACTIVE
        ]
        if not active:
            raise CapabilityConflictError("an active ProjectContract is required")
        return max(active, key=lambda item: item.version)

    async def _locks(self, project_id: str) -> list[CapabilityPackLock]:
        records = await self.store.list(project_id=project_id, object_type="capability_pack_lock")
        return [item for item in records if isinstance(item, CapabilityPackLock)]

    async def _lock(self, project_id: str, version: int) -> CapabilityPackLock:
        record = await self.store.get("capability_pack_lock", f"lock_{project_id}", str(version))
        if not isinstance(record, CapabilityPackLock):
            raise CapabilityNotFoundError(f"lock_{project_id}@{version}")
        return record

    async def _design_contract(
        self, project_id: str, lock_id: str, lock_version: int
    ) -> ProjectDesignContract:
        record = await self.store.get(
            "project_design_contract", f"pdc_{project_id}", str(lock_version)
        )
        if not isinstance(record, ProjectDesignContract) or record.lock_id != lock_id:
            raise CapabilityNotFoundError(f"pdc_{project_id}@{lock_version}")
        return record

    @staticmethod
    def _compile_contract(project_id, lock, packs, project_contract, actor):
        def combined(key):
            values = []
            for pack in packs:
                content = pack.content.get(key, [])
                values.extend(content if isinstance(content, list) else [])
            return values

        tokens = {}
        for pack in packs:
            tokens.update(pack.content.get("design_tokens", {}))
        layout = next(pack for pack in packs if pack.pack_type == "frontend-layout")
        return ProjectDesignContract(
            project_id=project_id,
            contract_id=f"pdc_{project_id}",
            version=lock.version,
            lock_id=lock.lock_id,
            lock_version=lock.version,
            spec_id=lock.spec_id,
            spec_version=lock.spec_version,
            framework=(project_contract.frameworks or [""])[0],
            pack_refs=lock.pack_refs,
            design_tokens=tokens,
            component_rules=combined("component_rules"),
            layout_rules=combined("layout_rules"),
            responsive_rules=combined("responsive_rules"),
            accessibility_rules=combined("accessibility_rules"),
            brand_rules=combined("brand_rules"),
            viewports=layout.content.get("viewports", ["1440x900", "768x1024", "390x844"]),
            validation_evidence=[
                "responsive screenshots at every locked viewport",
                "keyboard and accessible-name audit",
                "WCAG AA contrast audit",
                "visual regression comparison",
            ],
            migration_tasks=lock.migration_tasks,
            created_by=actor,
            source_ids=[lock.lock_id, *lock.pack_refs],
        )

    @staticmethod
    def _previews(name, tokens):
        return [
            {"surface": surface, "label": label, "theme": tokens, "title": name}
            for surface, label in (
                ("login", "登录页"),
                ("list", "列表页"),
                ("detail", "详情页"),
                ("mobile", "移动端"),
            )
        ]
