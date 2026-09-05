# LangGraph 多 Agent 研发平台三阶段 PRD V2

版本：2.0
日期：2026-09-04
状态：后续产品设计与开发的权威基线

## 1. 修订目的

本版本重新定义 LangGraph 控制中心的系统边界、模型接入方式和三阶段交付标准，解决旧方案中的以下问题：

- 把 ChatGPT Plus、ChatGPT Pro 账号通道误写成模型或普通 API Provider。
- 把物理主机、Controller、模型 Runner、Worker 和项目工作副本混为一层。
- 在没有真实 diff、测试和截图证据时提前调用审查、风险和监督角色。
- `.17`、`.31`、`.24` 三处代码关系没有可执行的同步与权威规则。
- TaskHub 任务状态、LangGraph 流程状态、Provider 调用状态混用。
- 初级、中级功能交叉，导致“初级已完成”与“模型编排仍是骨架”同时出现。
- Web 控制台可以写入密钥和重启服务，但尚无登录与操作授权。

V2 的核心原则是：模型负责产出候选结果，Worker 负责执行工具，TaskHub 保存事实，LangGraph 只根据事实推进流程，人负责高风险批准。

## 2. 产品目标

为 `douyin-listing-workbench` 建立一个可恢复、可审计、可人工接管的研发编排平台，并逐步替代原 OpenClaw PW 模式中的聊天式协作。

平台最终必须做到：

- 需求、计划、代码、测试、审查、验收和发布形成同一个可追踪 episode。
- Agent 或 Worker 中断后，系统能够判断重试、换通道、重新领取或人工接管。
- 监督角色只基于 Git、测试、截图和审查 artifact 裁决，不基于聊天印象。
- Plus、Pro 和 API 通道可以按角色路由和 fallback，但不泄露账号凭据。
- 每次自动操作都能回答：谁发起、谁执行、使用哪个模型和通道、修改了什么、证据在哪里。

## 3. 名词与边界

| 名词 | 定义 | 是否保存事实状态 |
| --- | --- | --- |
| LangGraph | 编排 episode、节点和条件分支 | 只保存流程检查点，不替代 TaskHub |
| TaskHub | 任务交换、状态历史、依赖和审计账本 | 是，任务状态唯一权威 |
| Controller | API、Web、编排、Provider Router | 否，通过 TaskHub/Postgres 读写事实 |
| Model | `gpt-5.6-sol`、`gpt-5.6-terra` 等推理能力 | 否 |
| Channel | Plus CLI、Pro CLI、GPT API、DeepSeek API、MiniMax API | 否，只记录调用状态 |
| Account Runner | 使用独立 ChatGPT 登录态调用 Codex CLI 的适配器 | 否，返回结构化结果和事件 |
| Worker | 执行 Git、测试、构建、浏览器等确定性工具 | 否，回写执行结果 |
| Artifact | diff、测试报告、日志、截图、计划和审查报告 | 是，按哈希关联任务 |
| Episode | 一个需求从接收到完成的端到端流程实例 | 是，拥有独立流程状态 |

强制约束：Plus/Pro 是订阅账号通道，不是模型；账号 Runner 不是通用 OpenAI API；Redis 不是任务事实来源。

## 4. 目标部署架构

### 4.1 控制面：`192.168.31.31 / WSL Ubuntu 24`

职责：

- LangGraph Controller 和 Web/API。
- TaskHub 与 Postgres。
- Provider Router 和 fallback 策略。
- Plus Codex CLI Runner。
- Pro Codex CLI Runner。
- GPT、DeepSeek、MiniMax API Adapter。
- episode checkpoint、审批和审计。

不承担：

- Windows GUI 自动化。
- 无审批生产发布。
- 把 Plus/Pro 凭据下发给 Worker。

### 4.2 执行面：`192.168.31.24`

职责：

- 领取确定性的工具任务。
- 在本机隔离工作区执行 Git、lint、typecheck、test、build。
- 执行 Playwright/H5 浏览器检查并产生截图。
- 回写退出码、摘要和 artifact 引用。

目标约束：中级阶段完成后，`.24` 不应再通过 SSH 到 `.31` 执行项目命令。Worker 必须在自己声明的执行环境中完成任务，否则 host、资源、权限和失败归因均不可信。

### 4.3 源码来源：`192.168.31.17`

当前事实：

- 存在 `.git` 目录，分支为 `openclaw/be012-review-r1`。
- 主机当前没有可用 `git` 命令。
- `.31` 工作副本没有配置 Git remote，且继承了既有 dirty/untracked 状态。

因此 V2 不再把 `.17` 简单描述为“可直接协作的 Git 远端”。在 Git 权威迁移完成前：

- `.17` 是历史源码和 OpenClaw 资料来源。
- `.31` 是现有 LangGraph 工作副本，但不是可靠的 canonical origin。
- 自动代码写入不得默认开启。

中级阶段的 P0 前置任务是建立唯一 operational Git authority。推荐在 `.31` 建立受备份的 bare repository，`.31` 和 `.24` 都从该 origin 创建独立 worktree；`.17` 保留为迁移来源和归档。迁移必须经过人工确认，不在本 PRD 中自动执行。

### 4.4 Redis 定位

当前 TaskHub 使用 Postgres，Redis 只参与健康检查。V2 规定：

- Postgres 是任务、episode、审批和调用记录的唯一事实来源。
- Redis 在没有承担事件广播、短期锁或缓存前应标记为 `optional/unused`，不能显示为核心队列。
- 即使未来使用 Redis，也不得只在 Redis 中保存不可恢复状态。

## 5. 模型与通道设计

### 5.1 角色配置

| 优先级 | 角色 | 主要职责 | 首选模型族 |
| --- | --- | --- | --- |
| 1 | 施工 `coder` | 生成代码补丁、修复缺陷 | `gpt-5.6-sol` |
| 2 | 监督 `supervisor` | 架构准入、返工或接受裁决 | `gpt-5.6-sol` |
| 3 | 规划 `planner` | 需求拆解、依赖、验收标准 | `gpt-5.6-terra` |
| 4 | 风险 `risk` | 汇总上下文、风险和验收证据 | `MiniMax-M3` |
| 5 | 审查 `reviewer` | 审查真实 diff、测试和逻辑漏洞 | `deepseek-v4-pro` |

优先级编号只表示资源和产品重要性，不表示执行顺序。

### 5.2 通道路由

| 角色 | 首选通道 | 第二通道 | 最终 fallback |
| --- | --- | --- | --- |
| 施工 | Pro CLI | Plus CLI | GPT API |
| 监督 | Plus CLI | Pro CLI | GPT API |
| 规划 | Plus CLI | Pro CLI | GPT API |
| 审查 | DeepSeek API | GPT API | 人工审查 |
| 风险 | MiniMax API | DeepSeek API | GPT API |

每个账号 Runner 使用独立 `CODEX_HOME`、独立登录缓存和单任务并发锁。TaskHub 与 Web 不保存或展示 `auth.json` 内容。

### 5.3 Provider 状态

账号和 API 通道必须由系统探测，不能由用户手工伪造“已连接”：

```text
disabled
available
busy
cooldown
quota_exceeded
needs_reauth
rate_limited
network_error
misconfigured
```

允许用户进行的操作只有：启用、禁用、发起登录、测试连接、调整路由。`connected`、`quota_exceeded` 等运行状态由 Adapter 根据真实调用事件更新。

订阅账号通常没有可靠的精确剩余额度接口。Web 只能显示“可用、冷却、额度触发、未知”，不得显示虚构百分比。

### 5.4 Fallback 规则

- fallback 的单位是一次角色调用，不是整个 episode。
- 鉴权失败直接标记 `needs_reauth`，不进行无意义重复调用。
- 额度或限流进入 cooldown，并切换到下一条兼容通道。
- 网络错误允许短退避重试一次，然后 fallback。
- 输出 schema 不合法允许同通道纠正一次，然后 fallback。
- 所有 attempt 必须记录角色、模型、通道、开始/结束时间、结果类型和错误分类。
- API fallback 成功不代表前一个账号“永久不可用”。

## 6. 正确的五角色编排

旧流程 `planner -> coder -> reviewer -> risk -> supervisor` 在一次规划请求中连续执行，审查者没有真实代码证据，属于伪审查。V2 改为按 artifact 驱动：

```text
需求接收
  -> 上下文与 Git 快照
  -> Planner 生成计划、依赖和验收标准
  -> Supervisor 审查计划
  -> Human Gate（高风险或范围不清时）
  -> Coder 在隔离工作区生成 patch
  -> Worker 应用 patch 并执行质量命令
  -> Reviewer 审查真实 diff + 测试结果
  -> Risk 汇总证据、未解决风险和发布影响
  -> Supervisor 最终裁决
       -> 接受
       -> 返工到 Coder
       -> 追加测试
       -> 转人工
  -> Human Gate（合并/部署）
  -> 完成
```

禁止规则：

- 没有 `context_snapshot`，Planner 不得产生执行计划。
- 没有 `patch/diff`，Reviewer 不得宣称代码审查通过。
- 没有测试 artifact，Supervisor 不得宣称实现完成。
- Risk 只能汇总证据，不得替代 Supervisor 作最终决策。
- 模型输出不能直接把 TaskHub 任务改为 `succeeded`。

## 7. 三层状态模型

### 7.1 Task 状态

一期保持兼容：

```text
pending -> running -> succeeded
                   -> failed
                   -> blocked
pending/failed/blocked -> canceled
failed/blocked -> pending
```

中级增加 lease，而不是随意增加状态：`lease_owner`、`lease_expires_at`、`heartbeat_at`。Worker 失联后由 Reaper 释放或重试任务，避免永久卡在 `running`。

### 7.2 Episode 状态

```text
intake
planning
awaiting_plan_approval
implementing
verifying
reviewing
awaiting_final_approval
completed
blocked
canceled
```

### 7.3 Provider Attempt 状态

```text
queued
running
succeeded
retryable_failed
exhausted
needs_reauth
canceled
```

三类状态分别存储，禁止用 Task 的 `blocked` 表示账号额度、流程审批和 Worker 故障三种不同事实。

## 8. 初级阶段：可靠任务底座

### 8.1 产品目标

单项目、单 Controller、至少一个 Worker 可以安全完成“创建、领取、执行、失败、恢复、人工接管”的闭环。初级阶段不依赖 LLM 才能成立。

### 8.2 P0 能力

- Postgres TaskHub、原子 claim、状态历史。
- Worker 注册、能力声明、心跳。
- 任务 lease、超时回收和幂等键。
- Web 总览、任务列表、详情、重试、阻塞、取消和人工批准。
- API 与 Web 的最小身份认证和写操作授权。
- 敏感字段在写入前、日志中、返回时三层脱敏。
- 项目上下文快照必须记录来源 host、路径、Git HEAD 或内容哈希。
- 危险任务采用 allowlist，生产发布保持禁用。
- 审计记录包含 actor、来源 IP、动作、原因和 request ID。

### 8.3 当前状态判断

当前不是“初级完全完成”，而是“初级功能 Beta，已提前进入部分中级功能”。已完成任务账本、基础状态机、Worker 轮询、Web 控制台、人工审批和受控命令；以下项目必须作为初级收口：

1. Web/API 登录与写操作授权。
2. `running` 任务 lease、Worker 失联回收。
3. 创建任务幂等键，防止重试生成重复任务。
4. 明确上下文快照和工作副本 commit/hash。
5. 将账号状态从手工填写改为 Adapter 探测。
6. UI 明确 Redis 当前是否实际参与运行。

### 8.4 完成标准

- 杀掉 Worker 后，超时任务可自动恢复且不会重复执行已提交结果。
- 未登录用户不能写 Key、修改 Provider、批准任务或重启服务。
- 相同 idempotency key 重复提交只产生一个任务。
- 任意执行结果能追溯到准确代码快照。
- 一期回归用例全部通过且无明文秘密进入 TaskHub。

## 9. 中级阶段：证据驱动的研发流水线

### 9.1 产品目标

让一个真实需求在人工控制下完成计划、开发、测试、审查和返工闭环，替代 OpenClaw PW 模式的主要开发协作。

### 9.2 P0 能力

- Plus/Pro 独立 Codex CLI Account Runner 和真实登录探测。
- Provider Router、fallback、并发锁、cooldown 和额度提醒。
- 任务依赖 DAG，只有依赖成功后才可领取。
- operational Git authority 和每任务隔离 worktree。
- Coder 产出 patch artifact，不直接改共享 dirty 工作树。
- Worker 本机执行 patch、lint、typecheck、test 和 build。
- Reviewer 只消费真实 diff/test artifact。
- Supervisor 支持 `accept/rework/add_tests/escalate` 决策。
- Playwright H5 桌面/手机检查、截图和控制台错误 artifact。
- artifact 存储、哈希、大小、MIME、保留策略。
- episode 页面展示节点、依赖、当前门禁和下一步，而不是图形化编辑 LangGraph。

### 9.3 P1 能力

- OpenClaw Adapter 只作为迁移桥接器，默认不作为状态中心。
- 可编辑的表格式执行计划，不要求用户直接编辑 JSON。
- 按任务类型统计模型成功率、fallback 次数、延迟和返工率。
- 模型 prompt/schema 版本化。

### 9.4 完成标准

- 一个 H5 功能需求能自动建立 episode 和依赖任务。
- Planner、Coder、Reviewer、Risk、Supervisor 在正确证据阶段运行。
- Pro 或 Plus 额度触发后能自动 fallback，任务和 episode 不丢失。
- 每个代码任务使用隔离 worktree，不能污染其他任务。
- 至少完成一次真实需求的“生成 patch、测试、审查、返工、人工接受”演练。
- 不执行自动生产发布，不自动发布抖店商品。

## 10. 高级阶段：多项目治理与生产控制

### 10.1 产品目标

把单项目研发流水线升级为多项目、可度量、可治理、可回滚的生产平台。

### 10.2 P0 能力

- 多项目隔离、RBAC、项目级密钥和 Worker 权限策略。
- 高可用 Controller/Worker 与数据库备份恢复演练。
- 长期记忆带来源、版本、有效期和可删除机制。
- 模型成本、账号可用性、API 消耗、成功率、延迟、返工率面板。
- 自动回归矩阵和视觉基线管理。
- release candidate、审批、部署、健康检查和回滚状态机。
- 生产动作双重门禁、最小权限凭据和完整审计。
- Agent 评估集、回放、A/B 路由和 prompt 回归测试。

### 10.3 完成标准

- episode 可以跨天恢复，Controller 或 Worker 重启不丢状态。
- 多项目之间的代码、artifact、密钥和账号权限不能串用。
- 所有生产变更关联 commit、测试、审批人、部署结果和回滚点。
- 能用历史数据回答不同模型/通道在各任务类型上的质量与成本。
- 完成备份恢复、Worker 掉线、Provider 故障和部署回滚演练。

## 11. Web 产品信息架构

### 初级

- 总览。
- 任务。
- Worker。
- 人工审批。
- 系统设置。

### 中级新增

- Episode 流程详情。
- Artifact/diff/test/screenshot 查看器。
- 模型与通道状态。
- fallback/cooldown/重新登录提醒。
- Git 工作区与 worktree 状态。

### 高级新增

- 项目空间和成员权限。
- 成本、质量和可靠性分析。
- 发布与回滚中心。
- 评估和历史回放。

安全要求：密钥字段只允许覆盖写入，永不回显；Plus/Pro 登录凭据不通过 Web 上传；服务重启、账号登录、批准和生产操作必须进入审计。

## 12. 数据与接口新增需求

中级前需要补充以下核心对象：

- `episodes`：流程实例和当前阶段。
- `task_dependencies`：任务依赖关系。
- `task_leases` 或任务 lease 字段。
- `provider_attempts`：每次模型/通道调用。
- `artifacts`：证据元数据和内容地址。
- `approvals`：批准对象、版本和决策。
- `context_snapshots`：项目上下文来源和哈希。
- `audit_events`：配置、登录、重启、批准和高风险操作。

所有写接口支持 `request_id`；创建任务和 episode 支持 `idempotency_key`；审批必须绑定被审批计划或 artifact 的版本哈希，防止“审批后内容被替换”。

## 13. 开发顺序

### 当前立即执行：初级收口

1. 登录、授权和审计。
2. lease、心跳回收和幂等。
3. Git/context snapshot 权威标识。
4. 账号状态只读探测，移除手工伪状态。

### 然后进入中级

1. 建立 operational Git authority 和隔离 worktree。
2. 接入 Plus/Pro CLI Runner。
3. 建立 Provider Attempt 与 fallback 状态机。
4. 重写五角色 artifact 驱动编排。
5. 加入 Worker 本地执行、测试和 H5 证据。
6. 完成一次真实功能 episode 验收。

### 最后进入高级

只有中级真实 episode 稳定运行后，才开发多项目、成本治理、长期记忆和生产发布，避免在错误底座上堆叠功能。

## 14. 明确不做

- 不把 LangGraph 做成另一个聊天窗口。
- 不让五个模型对同一份需求重复发表无证据意见。
- 不用模型输出替代测试和 Git 证据。
- 不让 `.24` 名义执行、实际 SSH 到 `.31` 执行成为长期架构。
- 不把 Redis 在线等同于任务系统可靠。
- 不显示虚构的 Plus/Pro 剩余额度。
- 不在共享 dirty 工作树上并行自动改代码。
- 不在中级完成前自动部署生产或发布抖店商品。

## 15. 决策记录

- V2 取代 `migration-three-stage-plan.md` 中的未来阶段规划与架构定义。
- 原初级 TaskHub、Web PRD 保留为历史实施基线；发生冲突时以本文件为准。
- `stage1-implementation-status.md` 继续记录已实现事实，不作为未来架构定义。
- Git authority 迁移属于独立高风险操作，需用户明确批准后实施。
