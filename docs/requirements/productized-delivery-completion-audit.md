# TaskHub V2 产品化交付 Phase 0–6 完成性审计

审计日期：2026-09-12

审计范围：`productized-delivery-orchestration.md` 第 18 节 Phase 0–6，以及第 16、19、20、
23 节与阶段验收直接相关的约束。

## 阶段证据

| 阶段 | 提交 | 当前实现证据 | 自动验证证据 | 结论 |
| --- | --- | --- | --- | --- |
| Phase 0 | `3e633cf42bfc3ac0e724e6cd76ea57f0e2b34b52` | `domain/production.py`、统一生产对象仓储、增量 PostgreSQL 迁移、Legacy Linear Plan、功能开关 | `test_production_baseline.py` 覆盖六类对象、状态转换、不可变内容、内存重载和旧运行映射 | 完成 |
| Phase 1 | `957f353126c9fb37940e6a9666c6822f919277e3` | Requirement、ProductSpec 版本、产品决策、差异和运行精确绑定 | `test_productization.py` 覆盖未批准阻断、批准后不可变、单一合并决策、追加补充和版本差异 | 完成 |
| Phase 2 | `ebdb5c9d501dff7f1568b8bd27e033dae7deb866` | 五类 ProjectContract 档案、三份机器文档、执行命令和结构门禁 | `test_project_contracts.py` 故意注入跨层依赖、循环、API、迁移、安全、二进制和缺失制品违规 | 完成 |
| Phase 3 | `39742df1aaa868c6aad6302fbd3c93d2f4fc2b43` | 持久 DAG、Ready、资源锁、动态批次、TaskAttempt、节点路由、幂等恢复 | `test_dag_orchestration.py` 覆盖并行、依赖等待、冲突串行、失败新 attempt、成功结果恢复不重跑和公平预算 | 完成 |
| Phase 3A | `29debe1b53df5d7ae7532e15508685f2459f419a` | 独立 React 生产画布、版本化拓扑、有类型连线、资源池、调度 allowlist、运行叠加 | `test_topologies.py` 覆盖恢复、合法/非法激活和路由；`test_topology_browser.py` 覆盖画布及等价入口 | 完成 |
| Phase 4 | `ac21f9a6012700426e03c64ab3387a7724804242` | ChangeRequest、影响子图、计划 vN+1、任务替代、回归和证据复用 | `test_revisions.py` 覆盖局部重跑、旧 attempt 保留、自动修订上限、CSRF 和人工批准 | 完成 |
| Phase 5 | `d3f6e902efe632854dc4e6903c9294e1806f3a5a` | 受信能力包、三套推荐、版本锁、ProjectDesignContract、任务上下文和迁移 | `test_capabilities.py` 覆盖不可信/不兼容输入、精确锁、规格换版、显式升级和 worker 注入 | 完成 |
| Phase 6 | `620c01afb8b372a17572e08098c576ed7d32c8a3` | 20 全局并发、项目策略、加权公平、成本门禁、千任务 Ready、关键路径/瓶颈和分页 | `test_scale_policy.py` 覆盖 20 并发、不饥饿、预算冻结及 1,000 任务 <2 秒；生产就绪与正式交付合同另有专项测试 | 完成 |

## 总体验收场景映射

1. 需求接入、结构化 ProductSpec、合并待决策和三套兼容前端推荐由
   `test_productization.py`、`test_capabilities.py` 共同验证。
2. 规格和设计锁批准、无环 DAG、任务合同及能力包上下文由 `test_capabilities.py` 与
   `test_dag_orchestration.py` 验证。
3. 无依赖任务并行、上游验证后 Ready、资源冲突串行和失败新 attempt 由
   `test_dag_orchestration.py` 的真实异步调度器用例验证。
4. 局部修订、未受影响成果复用和历史 attempt 不覆盖由 `test_revisions.py` 验证。
5. 运行恢复由 DAG 成功结果重载、LangGraph 新实例 checkpoint 恢复和 PostgreSQL 恢复用例
   分层验证；重复执行不会重复调用已成功工作包。
6. 设计令牌、组件、响应式、无障碍证据声明和 worker 上下文由能力包测试验证；当前真实
   Chrome 同时覆盖产品规格、DAG、拓扑、修订和能力包页面的 1440/680/390px 布局。
7. 架构、API、迁移、安全和制品门禁由 ProjectContract 违规夹具验证，运行评审保留结构化
   evidence；发布审批、权威仓库提交和失败恢复由 `test_workflow.py`、`test_deployment.py`
   验证。
8. 合法拓扑持久化、非法连线阻断、执行/测试资源 allowlist 和画布非右键等价入口由
   `test_topologies.py`、`test_topology_browser.py` 验证。生产拓扑与任务 DAG 保持独立事实层。

## 非功能与运维证据

- 完整 Python 回归：`271 passed, 19 skipped`。
- 当前真实浏览器回归：10 项通过，覆盖 Phase 1、3、3A、4、5、6 的产品化界面。
- `taskhub-web` 的 TypeScript/Vite 生产构建通过。
- Phase 6 的 1,000 任务 Ready 聚合与 DAG 分析均低于 2 秒；任务文本视图服务端分页。
- RBAC、CSRF、登录限制、会话撤销、Secure Cookie、安全头和审计由
  `test_access_security.py`、`test_operations.py` 验证。
- 正式 Compose Socket Proxy、跨平台升级/恢复脚本、校验和及加密密钥—数据库身份由
  `test_release_delivery.py` 验证；节点升级的健康失败自动回滚由 `test_remote_nodes.py` 验证。
- 两个 Git 远端的 `main` 均与本地提交一致；各阶段均为独立顺序提交。

## 明确边界

本目标明确要求不构建或上传 Docker 镜像，因此没有执行真实目标主机的镜像构建、正式发布
或业务数据恢复演练。相关代码、脚本、失败回滚和身份匹配合同已自动验证，但实际演练仍是
未来获得单独部署授权后的运维门禁，不能用源代码测试冒充外部环境演练。

该边界不影响 Phase 0–6 的本次源代码开发目标完成，也没有修改任何 TaskHub 受管项目。
