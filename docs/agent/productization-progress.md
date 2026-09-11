# TaskHub V2 产品化编排开发进度

本文件是 `docs/requirements/productized-delivery-orchestration.md` 的持续开发续接点。每个
Phase 完成后更新，用于在会话上下文压缩或新会话中恢复准确状态。

| 阶段 | 状态 | 已交付边界 |
| --- | --- | --- |
| Phase 0 | 已完成 | 六类领域 Schema、状态不变量、统一仓储、旧线性兼容视图、功能开关 |
| Phase 1 | 已完成 | Requirement 原文与补充、ProductSpec 草稿/评审/批准/修订/差异、合并产品决策、运行版本绑定 |
| Phase 2 | 已完成 | ProjectContract、五类官方项目模板、机器文档、可执行结构/命令/交付物门禁、运行精确绑定 |
| Phase 3 | 待开发 | DAG、Ready 计算、资源锁、并行调度、TaskAttempt 与恢复 |
| Phase 3A | 待开发 | 独立 taskhub-web、生产画布、拓扑验证/激活与运行叠加 |
| Phase 4 | 待开发 | ChangeRequest、影响分析、增量修订和证据复用 |
| Phase 5 | 待开发 | 能力包库存、信任、兼容、锁定及前端设计包 |
| Phase 6 | 待开发 | 规模、配额、公平调度、预测、权限运维和前端渐进迁移 |

## 当前续接点

下一阶段严格从 Phase 3 开始。Phase 2 已提供版本化 ProjectContract、五类官方项目模板、
三份 `.taskhub` 机器文档、仓库结构/依赖/复杂度/数据访问/API 一致性/迁移/凭据/二进制/
命令/交付物门禁，以及开发流程中的项目契约治理卡。功能开关启用后，运行必须同时绑定
已批准 ProductSpec 和已生效 ProjectContract 的明确编号、版本及不可变上下文。尚未生成
真实规划 DAG；功能开关
`TASKHUB_PRODUCTION_ORCHESTRATION_ENABLED` 默认保持 `false`，关闭时继续使用旧线性流程。

开发发布约束：每阶段完成后只提交并推送 `.3 Git` 与 GitHub；暂不部署、不构建 Docker
镜像、不上传镜像仓库。
