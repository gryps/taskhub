from taskhub_v2.domain.capability import CapabilityPack
from taskhub_v2.domain.project_contract import ProjectContract
from taskhub_v2.services.capability_catalog import FRONTEND_TYPES


def compatibility_report(contract: ProjectContract, packs: list[CapabilityPack]) -> dict:
    reasons = []
    by_type = {pack.pack_type: pack for pack in packs}
    project_languages = {item.lower() for item in contract.languages}
    project_frameworks = {item.lower() for item in contract.frameworks}
    missing = sorted(set(FRONTEND_TYPES) - set(by_type))
    if missing:
        reasons.append("missing frontend pack types: " + ", ".join(missing))
    if len(by_type) != len(packs):
        reasons.append("only one pack per type can be selected")
    refs = {pack.ref for pack in packs}
    for pack in packs:
        compatible = pack.compatibility
        if "1.1" not in compatible.taskhub_spec:
            reasons.append(f"{pack.ref} does not support TaskHub spec 1.1")
        if compatible.project_profiles and contract.profile_id not in compatible.project_profiles:
            reasons.append(f"{pack.ref} does not support profile {contract.profile_id}")
        supported_languages = {item.lower() for item in compatible.languages}
        supported_frameworks = {item.lower() for item in compatible.frameworks}
        if supported_languages and not supported_languages & project_languages:
            reasons.append(f"{pack.ref} does not support project languages")
        if supported_frameworks and not supported_frameworks & project_frameworks:
            reasons.append(f"{pack.ref} does not support project frameworks")
        conflicts = refs & set(compatible.incompatible_with)
        if conflicts:
            reasons.append(f"{pack.ref} conflicts with {', '.join(sorted(conflicts))}")
    return {
        "compatible": not reasons,
        "reasons": reasons,
        "profile": contract.profile_id,
        "languages": contract.languages,
        "frameworks": contract.frameworks,
    }
