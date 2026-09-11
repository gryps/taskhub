# TaskHub V2 产品化编排开发进度

本文件是 `docs/requirements/productized-delivery-orchestration.md` 的持续开发续接点。每个
Phase 完成后更新，用于在会话上下文压缩或新会话中恢复准确状态。

| 阶段 | 状态 | 已交付边界 |
| --- | --- | --- |
| Phase 0 | 已完成 | 六类领域 Schema、状态不变量、统一仓储、旧线性兼容视图、功能开关 |
| Phase 1 | 已完成 | Requirement 原文与补充、ProductSpec 草稿/评审/批准/修订/差异、合并产品决策、运行版本绑定 |
| Phase 2 | 已完成 | ProjectContract、五类官方项目模板、机器文档、可执行结构/命令/交付物门禁、运行精确绑定 |
| Phase 3 | 已完成 | 版本化 DAG、计划不变量、Ready/资源锁、动态并行批次、公平预算、TaskAttempt、故障转移与幂等恢复 |
| Phase 3A | 已完成 | 独立 taskhub-web、版本化生产画布、类型连线、拓扑路由、资源池与运行叠加 |
| Phase 4 | 已完成 | ChangeRequest、可解释影响子图、增量计划版本、任务替代、回归范围、证据复用与修订上限 |
| Phase 5 | 已完成 | 受信能力包库存、兼容解析、三方案预览、精确版本锁、项目设计合同和升级迁移 |
| Phase 6 | 待开发 | 规模、配额、公平调度、预测、权限运维和前端渐进迁移 |

## 当前续接点

下一阶段严格从 Phase 6 开始。Phase 5 已建立平台级 CapabilityPack 库存和项目级
CapabilityPackLock。内置或管理员导入的包具有固定语义版本、来源、兼容矩阵、内容摘要、
许可证、资产授权和执行权限声明；导入阶段阻止路径穿越、凭据和未申明的高权限内容。

前端项目根据活动 ProjectContract 获得最多三套兼容方案。用户选择后先形成与 ProductSpec
精确版本绑定的锁草稿，再明确激活并编译 ProjectDesignContract。设计令牌、组件、布局、
响应式、无障碍、品牌规则和验证证据进入 ExecutionPlan、ProductionTask 及工作节点上下文。
升级先生成迁移任务，已批准规格必须创建修订，旧锁和旧合同不被静默覆盖。

开发流程提供方案预览、版本锁和迁移卡片，系统配置提供全局库存管理。真实 Chrome 的
1440、680、390 像素操作已通过；完整 Python 套件为 `263 passed, 19 skipped`，
TypeScript/Vite 生产构建、Ruff、JavaScript 语法和架构约束均通过。

开发发布约束：每阶段完成后只提交并推送 `.3 Git` 与 GitHub；暂不部署、不构建 Docker
镜像、不上传镜像仓库。
