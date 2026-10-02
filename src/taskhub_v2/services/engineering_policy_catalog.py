from taskhub_v2.domain.governance import EngineeringRule

FRONTEND_PROFILES = {"frontend-spa", "fullstack-web"}


def builtin_rules() -> list[EngineeringRule]:
    return [
        EngineeringRule(
            rule_id="foundation.project-baseline",
            title="工程基线资料",
            category="foundation",
            instruction=(
                "在大规模功能开发前建立项目规则、架构、模块职责和架构决策记录；"
                "文档必须随代码边界变化同步更新。"
            ),
            required_paths=[
                "AGENTS.md",
                "docs/ARCHITECTURE.md",
                "docs/MODULES.md",
                "docs/DECISIONS/",
            ],
        ),
        EngineeringRule(
            rule_id="architecture.module-boundaries",
            title="模块边界与依赖方向",
            category="architecture",
            instruction=(
                "按业务域划分模块，通过公开接口协作；禁止跨模块访问内部实现、"
                "循环依赖和无归属 shared/utils 堆积。"
            ),
            max_file_lines=700,
            max_function_lines=100,
            max_complexity=15,
        ),
        EngineeringRule(
            rule_id="quality.batch-gates",
            title="批次质量门禁",
            category="quality",
            instruction=(
                "每个开发批次必须通过格式、静态检查、类型检查、测试和生产构建；"
                "不得删除测试、放宽断言或关闭类型检查来伪造通过。"
            ),
            required_command_groups=[
                "format",
                "lint",
                "type_check",
                "test",
                "architecture",
                "build",
            ],
        ),
        EngineeringRule(
            rule_id="delivery.touch-governance",
            title="触碰即治理",
            category="delivery",
            instruction=(
                "本次修改触及的区域必须停止架构恶化；越过预警线时同步拆分，"
                "例外必须记录原因、风险、控制措施和恢复条件。"
            ),
            manual_evidence="提供差异复核、文件规模和依赖方向检查结果。",
        ),
        EngineeringRule(
            rule_id="frontend.foundation",
            title="前端工程与设计基线",
            category="frontend",
            applies_to=sorted(FRONTEND_PROFILES),
            instruction=(
                "前端先明确用户任务、信息架构、视觉方向、状态和数据边界；"
                "页面负责组装，业务规则、请求和复杂状态进入功能模块。"
            ),
            required_paths=[
                "docs/PRODUCT.md",
                "docs/DESIGN.md",
                "docs/FRONTEND_ARCHITECTURE.md",
            ],
            max_file_lines=400,
        ),
        EngineeringRule(
            rule_id="frontend.release-verification",
            title="前端发布验证",
            category="frontend",
            applies_to=sorted(FRONTEND_PROFILES),
            instruction=(
                "验证加载、空、错误、无权限和极端数据状态，并实际检查 1440、768、"
                "390 像素视口、键盘操作、对比度和 reduced-motion。"
            ),
            required_command_groups=["test", "build", "acceptance"],
            manual_evidence="提供桌面、中间和手机视口以及键盘/无障碍检查证据。",
        ),
        EngineeringRule(
            rule_id="security.delivery-hygiene",
            title="安全交付检查",
            category="security",
            instruction=(
                "交付前检查实际差异，不得包含密钥、凭据、调试代码、临时产物或与任务无关的改动。"
            ),
            required_command_groups=["security"],
        ),
    ]
