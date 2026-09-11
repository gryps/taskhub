from taskhub_v2.domain.production import ProductQuestion


def subject(text: str) -> str:
    return (text.splitlines()[0] if text else "待确认产品目标")[:500]


def scope_items(text: str) -> list[str]:
    items = [line.lstrip("-*0123456789.、 ") for line in text.splitlines()[1:]]
    return [item for item in items if item][:20] or [text[:1_000]]


def non_functional_items(text: str) -> list[str]:
    keywords = ("性能", "安全", "可靠", "并发", "响应", "可用性", "performance", "security")
    return [line for line in text.splitlines() if any(key in line.lower() for key in keywords)]


def functional_items(text: str) -> list[str]:
    lines = [line.lstrip("-*0123456789.、 ") for line in text.splitlines()[1:]]
    excluded = (
        "验收",
        "成功标准",
        "完成标准",
        "性能",
        "安全",
        "可靠",
        "部署",
        "交付",
        "acceptance",
        "performance",
        "security",
        "deploy",
        "release",
    )
    items = [line for line in lines if line and not any(key in line.lower() for key in excluded)]
    return items[:50] or [subject(text)]


def risk_items(text: str) -> list[str]:
    keywords = ("风险", "迁移", "兼容", "删除", "敏感", "risk", "migration")
    return [line for line in text.splitlines() if any(key in line.lower() for key in keywords)]


def missing_information(text: str) -> list[ProductQuestion]:
    lowered = text.lower()
    checks = [
        (
            "target_users",
            ("用户", "角色", "使用者", "user", "persona"),
            "该功能主要由哪些用户或角色使用？",
            "用户角色影响权限、流程和界面设计。",
        ),
        (
            "acceptance_definition",
            ("验收", "成功标准", "完成标准", "acceptance"),
            "用什么可观察结果判定本需求已经完成？",
            "可执行验收标准是后续任务完成合同。",
        ),
        (
            "delivery_target",
            ("部署", "交付", "运行环境", "release", "deploy"),
            "期望交付到什么运行环境或形成什么制品？",
            "交付目标决定构建和部署边界。",
        ),
    ]
    return [
        ProductQuestion(key=key, prompt=prompt, reason=reason)
        for key, keywords, prompt, reason in checks
        if not any(keyword in lowered for keyword in keywords)
    ]
