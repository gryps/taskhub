# TaskHub V2 模块职责

本文补充 `docs/architecture.md`，记录本次全局工程治理涉及的稳定模块和数据所有权。

| 模块 | 职责 | 拥有的数据 |
| --- | --- | --- |
| `domain.governance` | 规则、冻结绑定和受控例外的不可变模型 | `EngineeringPolicy`、`PolicyBinding`、`PolicyException` |
| `services.engineering_governance` | 规则生命周期、合同编译、绑定校验和例外审批 | 规则版本及项目例外的业务操作 |
| `services.engineering_policy_catalog` | 首次部署的内置全局规则目录 | 内置 v1 规则定义 |
| `services.governance_gate` | 对仓库和项目合同执行可重复的规则检查 | 无持久状态，只生成门禁发现项 |
| `services.project_contracts` | 项目边界、命令和规则绑定的项目级权威 | `ProjectContract` |
| `services.dag_plan` | 将规格、合同及规则快照编译为计划与任务 | `ExecutionPlan`、`ProductionTask` |
| `services.dag_scheduler` | 按依赖调度批次并在集成后执行治理门禁 | `ExecutionBatch`、`TaskAttempt` |
| `workers.dag_executor` | 为编码节点组装冻结上下文并执行批次验证 | 工作区结果，不拥有规则 |

依赖方向为 API → 服务 → 领域/持久化。规则检查不读取 Codex 主机文件，也不直接写入受管项目。
