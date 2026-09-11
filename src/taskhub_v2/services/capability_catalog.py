from taskhub_v2.domain.capability import CapabilityPack, PackCompatibility

PLATFORM_PROJECT = "__platform__"
FRONTEND_TYPES = (
    "frontend-style",
    "frontend-components",
    "frontend-layout",
    "brand",
)


def builtin_capability_packs() -> list[CapabilityPack]:
    common = {
        "project_id": PLATFORM_PROJECT,
        "version": "1.0.0",
        "status": "trusted",
        "source": "builtin",
        "license": "Apache-2.0",
        "trusted_by": "taskhub-release",
    }
    web = PackCompatibility(
        project_profiles=["fullstack-web", "frontend-spa"],
        languages=["javascript", "typescript"],
    )
    return [
        CapabilityPack(
            **common,
            pack_id="pack_style_modern_enterprise",
            name="现代企业",
            pack_type="frontend-style",
            summary="克制、清晰且适合高密度业务操作的企业界面。",
            compatibility=web,
            files=["manifest.yaml", "design-tokens.json", "responsive.md"],
            content={
                "design_tokens": {"primary": "#1677ff", "radius": "8px", "density": "compact"},
                "responsive_rules": ["Desktop-first two-column cards", "Single column below 680px"],
            },
        ),
        CapabilityPack(
            **common,
            pack_id="pack_style_minimal",
            name="轻量极简",
            pack_type="frontend-style",
            summary="留白明确、层级简洁，适合门户和内容型产品。",
            compatibility=web,
            files=["manifest.yaml", "design-tokens.json"],
            content={
                "design_tokens": {"primary": "#111827", "radius": "12px", "density": "comfortable"},
                "responsive_rules": [
                    "Content width is bounded",
                    "Navigation collapses below 768px",
                ],
            },
        ),
        CapabilityPack(
            **common,
            pack_id="pack_style_data_dense",
            name="数据密集",
            pack_type="frontend-style",
            summary="强调指标、表格和状态对比的数据工作台风格。",
            compatibility=web,
            files=["manifest.yaml", "design-tokens.json", "visual-tests/dashboard.json"],
            validators=["visual-tests/dashboard.json"],
            content={
                "design_tokens": {"primary": "#0f766e", "radius": "6px", "density": "dense"},
                "responsive_rules": ["Metrics wrap without page overflow", "Tables scroll locally"],
            },
        ),
        CapabilityPack(
            **common,
            pack_id="pack_components_native_web",
            name="原生 Web 组件",
            pack_type="frontend-components",
            summary="无运行时 UI 依赖的语义化组件与键盘交互规则。",
            compatibility=web,
            files=["manifest.yaml", "components.md", "accessibility.md"],
            content={
                "component_rules": ["Use semantic controls", "Status needs text and color"],
                "accessibility_rules": ["Keyboard reachable", "WCAG AA contrast", "Visible focus"],
            },
        ),
        CapabilityPack(
            **common,
            pack_id="pack_layout_admin_console",
            name="管理控制台",
            pack_type="frontend-layout",
            summary="固定导航、紧凑卡片和渐进披露的后台管理布局。",
            compatibility=web,
            files=["manifest.yaml", "layouts.md", "responsive.md", "templates/list.html"],
            templates=["templates/list.html"],
            content={
                "layout_rules": [
                    "Persistent primary navigation",
                    "Cards own configuration and facts",
                ],
                "responsive_rules": ["Two columns on desktop", "One column at 680px"],
                "viewports": ["1440x900", "768x1024", "390x844"],
            },
        ),
        CapabilityPack(
            **common,
            pack_id="pack_layout_portal",
            name="产品门户",
            pack_type="frontend-layout",
            summary="面向产品入口、详情与内容发现的自适应门户布局。",
            compatibility=web,
            files=["manifest.yaml", "layouts.md", "responsive.md"],
            content={
                "layout_rules": ["Prominent product entry", "Readable detail hierarchy"],
                "responsive_rules": [
                    "Grid collapses progressively",
                    "Mobile actions stay reachable",
                ],
                "viewports": ["1440x900", "768x1024", "390x844"],
            },
        ),
        CapabilityPack(
            **common,
            pack_id="pack_brand_neutral",
            name="中性品牌基线",
            pack_type="brand",
            summary="可安全替换 Logo 和品牌色的中性品牌规则。",
            compatibility=web,
            files=["manifest.yaml", "rules/brand.md"],
            rules=["rules/brand.md"],
            content={
                "design_tokens": {"font": "system-ui", "logo_clearspace": "0.5x"},
                "brand_rules": ["Do not distort logos", "Do not encode status only by brand color"],
            },
        ),
    ]


RECOMMENDATION_COMBINATIONS = (
    (
        "现代企业控制台",
        "适合持续交付、运维和复杂业务配置。",
        "pack_style_modern_enterprise",
        "pack_layout_admin_console",
    ),
    ("轻量产品门户", "适合产品入口、内容与详情浏览。", "pack_style_minimal", "pack_layout_portal"),
    (
        "数据密集工作台",
        "适合指标、状态和高频操作。",
        "pack_style_data_dense",
        "pack_layout_admin_console",
    ),
)
