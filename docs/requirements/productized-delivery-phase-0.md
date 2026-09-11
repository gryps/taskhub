# TaskHub V2 产品化编排 Phase 0 技术基线

状态：已实现

## 范围

Phase 0 只建立事实模型与兼容边界，不改变现有 LangGraph 线性工作流的执行结果。

- 新增 ProductSpec、ExecutionPlan、Task、TaskAttempt、ChangeRequest、CapabilityPack Schema；
- 新增状态转换不变量；草稿允许受控编辑，批准或激活后的版本内容不可静默覆盖；
- 新增内存与 PostgreSQL 统一仓储；
- 新增旧运行到单 ProductSpec、单计划、单任务、单动态批次的兼容投影视图；
- 保留 `production_line=default`，不向用户重新暴露生产线输入；
- 增加 `TASKHUB_PRODUCTION_ORCHESTRATION_ENABLED` 功能开关，默认关闭。

## 数据库迁移与回滚

启动时执行增量、幂等迁移，创建：

- `taskhub_schema_migration`；
- `taskhub_production_object`；
- 项目与对象类型查询索引。

迁移不修改或删除 checkpoint、任务索引及现有业务表。回滚应用版本时将功能开关设为
`false` 即可；新增表保留为惰性数据，不参与旧流程。禁止通过自动回滚删除表。

## 后续边界

Phase 1 才会增加 Requirement、ProductSpec 草稿/评审/批准 API 与界面。Phase 0 的兼容
投影不得被描述成由规划器生成的真实 DAG，也不得伪造任务级验收证据。
