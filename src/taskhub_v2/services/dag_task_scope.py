from __future__ import annotations

from taskhub_v2.domain.project_contract import ModuleContract, ProjectContract

VERIFICATION_WORDS = (
    "test",
    "verify",
    "verification",
    "check",
    "accept",
    "review",
    "audit",
    "confirm",
    "测试",
    "验证",
    "核验",
    "检查",
    "验收",
    "复核",
    "审计",
    "确认",
)
DOCUMENTATION_WORDS = ("documentation", "document", "docs", "adr", "文档", "决策记录")
MODULE_ALIASES = {
    "frontend": (
        "frontend",
        " ui ",
        "ui/",
        "component",
        "button",
        "form",
        "dialog",
        "modal",
        "page",
        "visual",
        "responsive",
        "前端",
        "组件",
        "按钮",
        "表单",
        "弹窗",
        "页面",
        "视觉",
        "响应式",
    ),
    "web": (
        "frontend",
        " ui ",
        "ui/",
        "component",
        "button",
        "form",
        "dialog",
        "modal",
        "page",
        "visual",
        "responsive",
        "前端",
        "组件",
        "按钮",
        "表单",
        "弹窗",
        "页面",
        "视觉",
        "响应式",
    ),
    "client": (
        "frontend",
        " ui ",
        "ui/",
        "component",
        "button",
        "form",
        "dialog",
        "modal",
        "page",
        "visual",
        "responsive",
        "前端",
        "组件",
        "按钮",
        "表单",
        "弹窗",
        "页面",
        "视觉",
        "响应式",
    ),
    "ui": (
        "frontend",
        " ui ",
        "ui/",
        "component",
        "button",
        "form",
        "dialog",
        "modal",
        "page",
        "visual",
        "responsive",
        "前端",
        "组件",
        "按钮",
        "表单",
        "弹窗",
        "页面",
        "视觉",
        "响应式",
    ),
    "api": ("backend", " api ", "api/", "server", "后端", "接口"),
    "backend": ("backend", " api ", "api/", "server", "后端", "接口"),
    "server": ("backend", " api ", "api/", "server", "后端", "接口"),
    "worker": ("worker", "executor", "agent", "任务执行", "执行器"),
    "executor": ("worker", "executor", "agent", "任务执行", "执行器"),
}


def is_verification_step(step: str) -> bool:
    value = step.casefold()
    return any(word in value for word in VERIFICATION_WORDS)


def paths_for_step(step: str, contract: ProjectContract) -> list[str]:
    value = step.casefold()
    documented = _documentation_paths(value, contract.modules)
    if documented:
        return documented
    scored = [
        (_module_score(value, module), -index, module.paths)
        for index, module in enumerate(contract.modules)
    ]
    best = max(scored, default=(0, 0, []))
    if best[0] > 0:
        return list(best[2])
    if len(contract.modules) == 1:
        return list(contract.modules[0].paths)
    return sorted({path for module in contract.modules for path in module.paths})


def _documentation_paths(value: str, modules: list[ModuleContract]) -> list[str]:
    if not any(word in value for word in DOCUMENTATION_WORDS):
        return []
    return [
        path
        for module in modules
        if module.name in {"documentation", "docs"}
        for path in module.paths
    ]


def _module_score(value: str, module: ModuleContract) -> int:
    score = 0
    for term in _module_terms(module):
        if term in value:
            score += 4
        if any(word in value for word in MODULE_ALIASES.get(term, ())):
            score += 3
    return score


def _module_terms(module: ModuleContract) -> set[str]:
    terms = {module.name.casefold()}
    terms.update(
        part.casefold()
        for path in module.paths
        for part in path.replace("**", "").split("/")
        if part
    )
    return terms
