# TaskHub V2 产品化编排开发进度

本文件是 `docs/requirements/productized-delivery-orchestration.md` 的持续开发续接点。每个
Phase 完成后更新，用于在会话上下文压缩或新会话中恢复准确状态。

| 阶段 | 状态 | 已交付边界 |
| --- | --- | --- |
| Phase 0 | 已完成 | 六类领域 Schema、状态不变量、统一仓储、旧线性兼容视图、功能开关 |
| Phase 1 | 已完成 | Requirement 原文与补充、ProductSpec 草稿/评审/批准/修订/差异、合并产品决策、运行版本绑定 |
| Phase 2 | 已完成 | ProjectContract、五类官方项目模板、机器文档、可执行结构/命令/交付物门禁、运行精确绑定 |
| Phase 3 | 已完成 | 版本化 DAG、计划不变量、Ready/资源锁、动态并行批次、公平预算、TaskAttempt、故障转移与幂等恢复 |
| Phase 3A | 待开发 | 独立 taskhub-web、生产画布、拓扑验证/激活与运行叠加 |
| Phase 4 | 待开发 | ChangeRequest、影响分析、增量修订和证据复用 |
| Phase 5 | 待开发 | 能力包库存、信任、兼容、锁定及前端设计包 |
| Phase 6 | 待开发 | 规模、配额、公平调度、预测、权限运维和前端渐进迁移 |

## 当前续接点

下一阶段严格从 Phase 3A 开始。Phase 3 已把 Planner 输出编译为绑定 ProductSpec 与
ProjectContract 明确版本的持久化 ExecutionPlan、ProductionTask、ExecutionBatch、
TaskAttempt 和 DAG 快照。计划验证阻止非法依赖、循环、缺失验收、无来源输入、未消费的
中间输出、危险并行范围、合同越界、不可验证大任务及无安装策略的能力缺口。调度器按依赖、
冻结合同、治理状态、节点能力、资源锁与全局/项目预算计算动态批次，通过底层 NodeScheduler
继续执行节点优先级、槽位、能力、任务粘性与故障转移；成功持久化的结果在恢复时不会再次
执行命令。开发流程提供只读任务/批次卡片，API 提供分页计划视图。

功能开关 `TASKHUB_PRODUCTION_ORCHESTRATION_ENABLED` 默认保持 `false`，关闭时继续使用旧
线性流程。Phase 3A 尚未开发独立 `taskhub-web`、可编辑生产画布、拓扑版本和拓扑路由。

Phase 3 验证：完整 Python 套件 `244 passed, 10 skipped`；真实 Google Chrome 在 1440、
680 和 390 像素视口通过产品规格、项目契约与执行计划卡片布局、字体和页面横向溢出检查；
JavaScript 语法、Ruff、架构文件行数与 Git diff 检查通过。

开发发布约束：每阶段完成后只提交并推送 `.3 Git` 与 GitHub；暂不部署、不构建 Docker
镜像、不上传镜像仓库。
