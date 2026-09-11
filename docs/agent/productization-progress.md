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
| Phase 4 | 待开发 | ChangeRequest、影响分析、增量修订和证据复用 |
| Phase 5 | 待开发 | 能力包库存、信任、兼容、锁定及前端设计包 |
| Phase 6 | 待开发 | 规模、配额、公平调度、预测、权限运维和前端渐进迁移 |

## 当前续接点

下一阶段严格从 Phase 4 开始。Phase 3A 已建立独立 `taskhub-web`，以 React、TypeScript、
Vite、React Flow 和 TanStack Query 提供 `/canvas/` 生产画布。项目、Seed 控制器、执行、
测试、预生产和资源池节点可保存布局；连线具有固定业务类型。拓扑按 draft、validating、
active、superseded/invalid 持久化，同一项目只有一个活动版本，更新使用内容摘要防止并发覆盖。
服务端校验节点/边唯一性、端点、类型、循环、唯一控制路径、执行/测试路径、资源健康、角色
能力、资源重复绑定和同角色故障转移；Running 任务默认不迁移。

活动拓扑通过节点白名单限制后续 coding/test 调度；未激活拓扑时保持 Phase 3 调度行为。
画布运行叠加层读取最新运行、DAG 任务以及节点健康/槽位，项目卡可提交需求进入既有产品化
流程。工具栏、键盘上下文菜单、画布右键和节点/连线列表提供等价操作，窄屏保留列表配置。
现有任务中心、开发流程和系统配置不迁移，开发流程仅增加通往独立画布的入口。

Phase 3A 验证：完整 Python 套件 `249 passed, 13 skipped`、TypeScript/Vite 生产构建及真实 Google Chrome 1440、
680、390 像素画布操作和页面横向溢出检查通过；拓扑生命周期、非法边、重启读取、调度
白名单、原有产品化/调度/静态 DOM 回归均有自动化覆盖。具体最终计数见本阶段提交记录。

开发发布约束：每阶段完成后只提交并推送 `.3 Git` 与 GitHub；暂不部署、不构建 Docker
镜像、不上传镜像仓库。
