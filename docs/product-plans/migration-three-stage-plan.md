# LangGraph 迁移三阶段全案

> 历史说明：本文是第一版迁移规划，保留用于追踪原始目标。自 2026-09-04 起，系统架构、模型通道、角色编排和三阶段交付标准以 `docs/langgraph/prd-three-stage-v2.md` 为准；本文与 V2 冲突的部分不再作为开发依据。

## 1. 背景

当前真实项目在 `192.168.31.17:/home/gryps/.openclaw/workspace/douyin-listing-workbench`。

该项目是抖店管理平台，核心目标是把自有货源商品从资料录入、实拍素材、AI 图片生成、内容审核、供应链确认推进到 Windows 执行器，在抖店保存商品草稿。项目当前仍以 OpenClaw 工作区和 Workboard 为主要协作载体。

过去使用 OpenClaw 多 agent 协同 PW 模式时，已形成基本角色分工：

- `plan_A`：统一监督、规格、任务拆解、状态、门禁和进度。
- `plan_B`：开发、修订和 backlog/release 工作。
- `work_A`、`work_B`、`work_C`：测试验收和补充开发。
- MiniMax、DeepSeek、ChatGPT Pro、ChatGPT Plus 等模型按能力参与开发、评审和验收。

主要痛点不是模型能力不足，而是缺少一个强约束的任务交换中心：

- 任务分配容易停留在聊天约定里。
- 执行 agent 中途断线后，系统不能稳定恢复。
- 监督模型看到的是聊天内容，不是完整、实时、可信的任务状态。
- 代码、测试、验收、发布证据散落在工作树、任务单和对话里。
- 人工需要频繁补救 agent 丢失信号、未按模型分工执行、或者半途偏离任务的问题。

LangGraph 的定位应当是项目工作流总控，TaskHub 的定位应当是任务交换和状态账本。模型只负责推理和产出，不能再负责保存事实状态。

## 2. 总体目标

把原 OpenClaw 下的项目逐步迁移为 LangGraph 驱动的可恢复研发系统。

目标不是立即替换 OpenClaw，而是先让 LangGraph 接管状态、任务、门禁和恢复机制，再逐步把 OpenClaw 的 agent 经验、模型路由和 PW 协作协议沉淀为结构化工作流。

最终系统需要做到：

- 每个任务都有持久记录、状态、负责人、输入、输出、错误和审计轨迹。
- 每个 worker 或 agent 都通过 TaskHub 领取任务和回写结果。
- Supervisor 不再凭聊天感觉判断进度，而是读取任务状态和证据。
- 开发、测试、验收、发布都能被中断后恢复。
- 人可以随时查看、暂停、重试、阻塞、取消和接管任务。

## 3. 推荐部署拓扑

已建议并采用的 LangGraph 本地拓扑：

| 主机 | 角色 |
| --- | --- |
| `192.168.31.31` | LangGraph Controller、TaskHub、Postgres、Redis、主 API、主 worker |
| `192.168.31.24` | 副 worker、测试、浏览器自动化、工具节点 |
| `192.168.31.17` | 原 OpenClaw 项目仓库和迁移对象 |

硬性架构边界：

- 本次用 LangGraph 继续开发项目时，运行架构只由 `gryps@192.168.31.31` 和 `gryps@192.168.31.24` 组成。
- `192.168.31.31` 是唯一总控入口，负责任务状态、TaskHub、Postgres、Redis、API 和 LangGraph 编排。
- `192.168.31.24` 是 worker 节点，负责领取任务、执行测试、工具调用、浏览器自动化和后续可控执行。
- `192.168.31.17` 只作为原项目 Git 仓库、OpenClaw 工作区和项目资料来源，不承担 LangGraph controller 或 worker 职责。

项目权威：

- 当前项目仓库：`192.168.31.17:/home/gryps/.openclaw/workspace/douyin-listing-workbench`
- 工作区总目录：`192.168.31.17:/home/gryps/.openclaw/workspace`
- 当前状态文件：`PROJECT_STATE.md`
- 当前默认上下文：`CONTEXT_PACK.md`
- 项目任务记录：`tasks/`

迁移期间的原则：

- `.17` 项目仓库仍是项目事实来源。
- `.31` LangGraph 只做工作流总控和任务账本。
- `.24` worker 只领取受控任务，不直接绕过 TaskHub。
- 不得把 API Key、Cookie、抖店登录态、真实商品图或个人信息写入 TaskHub 明文日志。
- 一期仍只保存抖店草稿，不自动发布商品。

## 4. 模型角色设计

可用模型资源：

- ChatGPT Pro
- ChatGPT Plus
- MiniMax
- DeepSeek

建议职责：

| 角色 | 首选模型 | 备用模型 | 职责 |
| --- | --- | --- | --- |
| 总监督 | ChatGPT Pro | ChatGPT Plus | 架构审查、计划验收、风险判断、最终接受 |
| 需求拆解 | ChatGPT Pro | DeepSeek | 把业务需求拆成可执行任务和验收标准 |
| 开发实现 | DeepSeek | ChatGPT Plus | 编码、重构、单测、修复 |
| 后端实现 | MiniMax 或 DeepSeek | ChatGPT Plus | FastAPI、数据库、执行器契约 |
| 前端体验 | ChatGPT Plus | MiniMax | 手机端 H5 体验、页面文案、交互一致性 |
| 独立验收 | ChatGPT Pro / MiniMax | DeepSeek | 对具体 diff、测试结果、截图和证据做验收 |

关键规则：

- 模型不能自己决定最终任务状态。
- 任务状态只能由 LangGraph/TaskHub 根据明确事件改变。
- 模型输出必须绑定到任务 ID。
- 高价值模型用于决策和验收，低成本模型用于重复实现和测试。

## 5. 阶段一：初级

### 5.1 目标

建立最小可用 TaskHub，使原 OpenClaw 项目从聊天式协作转为任务式协作。

阶段一只解决可靠性底座：

- 任务能创建。
- worker 能领取。
- 结果能回写。
- 失败能保留。
- 人能查看和重试。
- LangGraph 能用状态机驱动一个最小研发流程。

### 5.2 必做能力

- TaskHub 数据表。
- Task 创建、查询、领取、完成、失败、重试、阻塞、取消 API。
- worker 心跳。
- worker 轮询领取任务。
- 任务状态机。
- 项目级任务模板。
- 初级 Web 控制台：总览、任务列表、任务详情、新建任务、worker 状态。
- OpenClaw 项目上下文只读同步。

### 5.3 阶段一状态机

```text
pending -> running
running -> succeeded
running -> failed
running -> blocked
failed -> pending
blocked -> pending
pending -> canceled
failed -> canceled
blocked -> canceled
```

禁止无约束跳转。所有跳转都必须记录时间、操作者、原因和结果。

### 5.4 阶段一 LangGraph 流程

```text
START
  -> receive_requirement
  -> classify_project_context
  -> create_task_plan
  -> create_taskhub_tasks
  -> dispatch
  -> wait_or_summarize
  -> END
```

阶段一不要求图长期阻塞等待 worker 完成。可以先采用异步模式：图负责建任务和汇总状态，worker 后台执行，用户或 supervisor 再触发下一轮汇总。

### 5.5 阶段一交付标准

- 能针对 `douyin-listing-workbench` 创建一个开发任务。
- `.24` worker 能从 `.31` TaskHub 领取任务。
- worker 完成后，TaskHub 里能看到结果。
- worker 失败后，TaskHub 里能看到错误并手动重试。
- 任务详情能关联项目、任务类型、输入、输出和证据。
- 用户可以通过 Web 创建任务、查看任务、重试失败任务、阻塞任务和查看 worker。
- 不直接改生产，不自动发布抖店商品。

## 6. 阶段二：中级

### 6.1 目标

把 TaskHub 从任务账本升级为多 agent 研发流水线。

阶段二开始接近原 OpenClaw PW 模式，但要比 PW 更可靠，因为所有协作都落到结构化任务和状态机。

### 6.2 必做能力

- 任务依赖关系。
- 多 worker 队列。
- 模型路由规则。
- OpenClaw adapter。
- Git worktree/branch 任务隔离。
- 代码 diff、测试结果、截图、日志作为任务 artifact。
- 浏览器自动化 H5 检查。
- 失败分类和自动重试策略。
- 人工审批门禁。
- 项目状态文件自动建议更新，但不自动覆盖。

### 6.3 典型流程

功能开发：

```text
需求
  -> 需求拆解
  -> 前端任务 / 后端任务 / 执行器任务 / 测试任务
  -> 并行开发
  -> 自动测试
  -> 手机 H5 截图验收
  -> 模型审查
  -> 人工接受
```

Bug 修复：

```text
问题描述
  -> 复现任务
  -> 根因定位
  -> 修复任务
  -> 回归测试
  -> 验收记录
```

发布准备：

```text
候选提交
  -> 质量门禁
  -> release artifact
  -> 发布清单
  -> 人工审批
  -> 部署任务
  -> 部署后复核
```

### 6.4 阶段二交付标准

- 一个需求可以自动拆成多个任务。
- 不同 worker 可以并行处理不同任务。
- supervisor 看到的是事实状态、diff、测试和 artifact。
- 失败任务能按类型自动重试或升级为人工介入。
- OpenClaw 能作为执行工具被调用，而不是继续作为唯一状态中心。

## 7. 阶段三：高级

### 7.1 目标

形成面向电商项目族的 agent 操作系统。

阶段三的重点是治理、评估、长期记忆、成本控制和生产级发布。

### 7.2 必做能力

- 多项目 TaskHub。
- 长期项目记忆，关联任务、文件、发布、事故和决策。
- 模型成本、额度、成功率和延迟统计。
- worker 权限策略。
- 文件访问策略。
- secret 脱敏策略。
- 自动回归测试矩阵。
- 视觉回归和移动端截图对比。
- 发布编排和回滚演练。
- agent 绩效评估。
- 知识库自动沉淀。
- 完整 Web 控制台。

### 7.3 阶段三交付标准

- 大型开发 episode 可以跨天恢复。
- worker 掉线不会丢任务。
- 模型评审基于 artifact，而不是聊天印象。
- 生产变更全部可追踪、可审批、可回滚。
- 能统计哪些模型、哪些 agent、哪些任务类型最可靠。

## 8. 从 OpenClaw 迁移的顺序

推荐顺序：

1. 保留 OpenClaw 当前项目和 Workboard，不破坏现有流程。
2. 在 `.31` 建 TaskHub 作为新任务账本。
3. 让 `.24` worker 跑最小任务闭环。
4. 把 `.17` 项目的 `PROJECT_STATE.md`、`CONTEXT_PACK.md`、`tasks/` 做只读同步。
5. 用 LangGraph 创建新任务，并把任务记录写入 TaskHub。
6. 让 worker 回写 artifact，而不是只在聊天里回复。
7. 增加 OpenClaw adapter，必要时调用原 OpenClaw agent 执行。
8. 把 plan_A 的监督职责迁移为 LangGraph 状态机和人工审批门禁。
9. 最后再考虑是否减少 OpenClaw Workboard 的使用。

## 9. 第一批任务类型

阶段一建议任务类型：

- `project.context.sync`：同步 `.17` 项目上下文。
- `requirement.split`：拆解需求。
- `code.change`：开发修改。
- `test.run`：运行测试。
- `h5.inspect`：手机 H5 页面检查。
- `review.model`：模型审查。
- `review.human`：人工验收。
- `ops.note`：记录运维事实。
- `openclaw.adapter.call`：调用 OpenClaw 既有能力。

## 10. 关键风险

- 不能把 LangGraph 做成另一个聊天系统，否则会重复 OpenClaw 的痛点。
- 不能让 worker 直接绕过 TaskHub 改项目。
- 不能把生产密钥、Cookie、登录态、真实商品图写进任务日志。
- 不能让模型自己宣布任务成功，必须绑定测试、diff 或人工验收。
- 不能一开始就追求全自动发布，当前业务规则仍是保存草稿，不自动发布。

## 11. 总结

初级阶段做任务可靠性，中级阶段做多 agent 研发流水线，高级阶段做生产级 agent 操作系统。

第一步不是让 LangGraph 变聪明，而是让任务不丢、状态可信、失败可恢复、人工可接管。
