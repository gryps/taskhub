# LangGraph Stage 1 Implementation Status

更新时间：2026-09-04

当前阶段：初级工程闭环已完成，并提前实现了部分中级 Provider/多角色原型。认证授权、审计、任务 lease/失联回收、幂等、代码快照和审批版本绑定已于 2026-09-04 上线；Plus/Pro 真实账号状态探测随 Codex CLI Runner 纳入中级阶段，不再由初级阶段的手工状态占位承担。

产品与架构基线：

- 后续开发以 `docs/langgraph/prd-three-stage-v2.md` 为准。
- 本文只记录已经实现和验证的事实，不再承担未来架构定义。
- Plus/Pro 是 Codex CLI 账号通道，不是模型，也不是普通 Chat Completions API Provider。
- 当前五角色在规划请求中顺序调用仍属于中级原型；Reviewer、Risk、Supervisor 尚未基于真实 diff、测试和截图执行，不能视为完整五角色研发闭环。

## 0. 初级工程闭环升级（2026-09-04）

已上线：

- Web/API 统一管理员认证：管理员令牌只用于换取 HttpOnly、SameSite 会话 Cookie；写请求同时校验 CSRF。
- Worker 使用独立 `TASKHUB_WORKER_TOKEN`，不共享管理员令牌；错误凭证无法 claim、heartbeat、complete 或 fail。
- 服务端从认证身份生成 actor，忽略客户端伪造的 actor；写操作写入 `taskhub_audit_events`。
- TaskHub claim 返回单次 lease token；Worker heartbeat 延长租约；租约过期后由 claim 流程自动回收为 pending 并增加 retry count。
- complete/fail 必须同时匹配 worker id 和 lease token；旧 Worker 的迟到结果会以 409 拒绝。
- Task 支持 `(project, idempotency_key)` 唯一幂等；图入口与 Planner 入口支持 episode 级幂等键，审批派生任务自动生成稳定幂等键。
- `review.human` 创建时保存 approval plan SHA-256；批准时校验原版本，并记录最终批准内容 SHA-256。
- 图入口与 Planner 派生任务写入 `.31` 工作副本 Git 快照：source host、repo root、workspace id、branch、完整 commit、dirty、status hash 和 change count。
- 敏感字段在写入任务、历史和审计表前递归脱敏；lease token 不返回管理员任务查询，只在 claim 响应中交给 Worker。
- `.24` Worker 默认 `CODE_APPLY_ENABLED=false`；真实 `git apply` 还要求审批内容哈希。初级阶段保持默认禁止真实写入。
- Web 明确标识 Redis 当前仅做健康检查；Plus/Pro account provider 当前为手工模拟状态且 CLI connector 尚未实现。

凭证文件：

- `.31:/home/gryps/apps/langgraph-control/.admin-token`，权限 `0600`，用于首次 Web 登录。
- `.31:/home/gryps/apps/langgraph-control/.worker-token`，权限 `0600`，只用于向 Worker 安全同步，不进入 Web。

验收结果：

- 匿名 API 返回 401；缺少 CSRF 的会话写请求返回 403。
- 固定 idempotency key 重复创建返回同一任务 ID。
- 审批 actor 无法由请求体伪造，历史记录为认证身份 `admin`。
- Worker 错误凭证返回 401；错误/旧 lease token 返回 409。
- 模拟租约过期后任务被新 Worker 自动重领；heartbeat 可续租。
- 普通 `ops.note` 任务仍可由 `.24` 完成；图级幂等和 Git 快照已验证。
- 真实代码 apply 默认拒绝，返回 `CODE_APPLY_DISABLED`。

中级启动记录：

- 中级第 1 项 Codex CLI Account Runner 已于 2026-09-04 完成代码与部署：`.31` 安装 `codex-cli 0.153.2`，Plus/Pro 分别使用 `/home/gryps/.codex-plus` 和 `/home/gryps/.codex-pro`，目录权限 `0700`。
- 新增 `app/account_runner.py`：通过 `codex login status` 自动探测账号，不再接受页面手工伪造连接/额度状态；通过 `codex exec --ephemeral --ignore-user-config --sandbox read-only --json --output-schema` 执行结构化调用。
- Plus/Pro Runner 使用独立互斥锁、600 秒默认超时，并识别 `needs_reauth`、`busy`、`quota_exceeded`、`rate_limited`、`network_error` 和 `runner_error`；失败后继续进入下一 provider fallback。
- Runner 调用环境主动移除 OpenAI/GPT API key，确保账号通道不会误用 API 计费凭证；prompt 通过 stdin 传入，不出现在进程命令行。
- 新增 `scripts/codex_account_login.sh` 和 `scripts/codex_account_status.sh`；Web“主管”页面显示自动状态、CLI 版本和登录命令，并支持真实账号测试。
- 当前 Plus/Pro 均处于 `needs_reauth`，需分别完成一次 device-auth 后，才能进行真实账号推理验收；未登录时现有 GPT API fallback 保持可用。

- LLM planner 控制器路由已开始接入。
- 当前新增 `/planner/providers`、`/planner/plan`、`/planner/invoke`。
- Planner 支持 OpenAI-compatible Chat Completions Provider：OpenAI、DeepSeek、MiniMax。
- 当 provider API key 未配置或调用失败时，自动回退到规则 planner。
- Planner 输出只进入 `review.human`，批准前可编辑 `approval_plan`；模型不会直接执行代码修改、质量命令或生产操作。
- Web 控制台已新增 Planner 页面，可查看 provider 配置状态并提交需求生成待审批计划。
- `.31:/home/gryps/apps/langgraph-control/.env.example` 已补充 LLM planner 配置占位符，不包含真实密钥。
- LLM planner fallback smoke 已验证：任务 `29fb462f-8916-424f-a4a5-2bc08f6cf81f` 已创建为 pending `review.human`，包含 4 步 `approval_plan`。
- 五角色 planner 配置层已上线：施工、监督、规划使用 `chatgpt_plus_account -> chatgpt_pro_account -> gpt_api` fallback；风险使用 `minimax_api`；审查使用 `deepseek_api`。
- 新增 `/planner/roles`、`/planner/multi-plan`、`/planner/multi-invoke`。`/planner/roles` 可查看角色模型、fallback 顺序、provider 状态和配置提醒。
- Provider fallback 已记录尝试明细和提醒码：账号未连接为 `ACCOUNT_SESSION_NOT_CONNECTED`，API 未配置为 `NOT_CONFIGURED`，API 401/403 为 `AUTH_FAILED`，API 429 可识别 `RATE_LIMITED` 或 `QUOTA_EXCEEDED`。
- Web Planner 页已增加角色表、fallback 顺序、配置提醒和提交结果中的 provider 尝试/提醒摘要。
- 五角色 planner smoke 已验证：任务 `84871d23-e478-42c3-bcc3-82a21effae30` 已创建为 pending `review.human`，包含 4 步 `approval_plan`、5 个 `role_outputs`、11 条 `provider_attempts`、11 条 `alerts`。
- Provider 连通性测试接口已上线：`POST /planner/providers/{provider}/test`。API 型 provider 会发起最小 `chat/completions` JSON 测试；账号型 provider 返回账号会话状态。
- `/planner/providers` 当前聚焦显示 `gpt_api`、`deepseek_api`、`minimax_api` 三个 API provider；Web Planner provider 卡片已增加“测试”按钮和测试结果输出。
- Provider test smoke 已验证：`gpt_api`、`deepseek_api`、`minimax_api` 在未配置 key 时返回 `NOT_CONFIGURED`；`chatgpt_plus_account`、`chatgpt_pro_account` 返回 `ACCOUNT_SESSION_NOT_CONNECTED`；测试不创建 TaskHub 任务。
- 角色模型 label 和真实 API model id 已分离。`/planner/roles` 现在返回每个角色在每个 provider 下的 `resolved_model`。
- `.env.example` 已增加按角色覆盖真实 API 模型的配置项：`ROLE_CODER_GPT_API_MODEL`、`ROLE_SUPERVISOR_GPT_API_MODEL`、`ROLE_PLANNER_GPT_API_MODEL`、`ROLE_REVIEWER_DEEPSEEK_API_MODEL`、`ROLE_RISK_MINIMAX_API_MODEL`。
- 模型映射 smoke 已验证：账号型 provider 继续显示用户角色模型 label；API 型 provider 默认解析到各自通用 model，后续可由角色级 env 覆盖。
- 安全配置检查接口已上线：`GET /planner/config-check`。接口只返回 `.env` 是否存在、权限是否安全、provider 是否配置、缺失 env 名称和角色 resolved model，不返回任何密钥值。
- `.31` 已新增本地交互式配置脚本：`/home/gryps/apps/langgraph-control/scripts/configure_llm_provider.py`，支持 `gpt_api`、`deepseek_api`、`minimax_api`，API key 通过隐藏输入读取，写入前备份 `.env`，写入后设置权限 `0600`。
- 配置检查 smoke 已验证：当前 `.env` 存在且权限为 `0600`；三个 API provider 均显示缺失对应 `*_API_KEY`；脚本 `--help` 可正常运行；配置检查不创建 TaskHub 任务。
- Web Provider Key 配置功能已上线：Planner 页面可选择 `gpt_api`、`deepseek_api`、`minimax_api`，输入 API key、base_url、默认 model 和角色 model 覆盖。API key 输入框为 password 类型，保存后清空输入框，页面只显示 key 掩码。
- 新增 `POST /planner/providers/{provider}/config`，只允许写 API 型 provider 相关 env 和白名单角色 model env。接口写入 `.env` 前自动备份，写入后设置权限 `0600`，并更新当前进程环境变量以便立即测试。
- Web key 配置 smoke 已验证：在不提交 API key 的情况下保存 `gpt_api` 非敏感字段成功，返回 `api_key_mask=""`、`configured=false`、缺失项仍为 `LLM_GPT_API_KEY`；TaskHub 队列未新增任务。

## 1. 运行架构

本次 LangGraph 继续开发架构：

```text
gryps@192.168.31.31
  LangGraph Controller
  TaskHub
  Postgres
  Redis
  Web/API
  douyin-listing-workbench 工作副本
  项目质量命令执行点

gryps@192.168.31.24
  Worker
  任务轮询
  测试执行
  工具执行
  项目上下文只读同步

192.168.31.17
  原 OpenClaw 项目 Git 仓库
  项目历史与原始工作区
  不作为 LangGraph controller 或 worker
```

当前约定：

- `.17` 保留为原 OpenClaw 项目仓库来源，不直接承担 LangGraph 运行。
- `.31:/home/gryps/apps/douyin-listing-workbench` 是 LangGraph 使用的项目工作副本。
- `.24` worker 通过 SSH 调用 `.31` 工作副本执行受控质量命令。

## 2. 已上线服务

Controller：

```text
http://192.168.31.31:8123
```

Worker：

```text
http://192.168.31.24:8124
```

Web 控制台：

```text
http://192.168.31.31:8123/
```

## 3. 已完成 Controller 能力

TaskHub API：

```text
GET  /health
GET  /workers
GET  /taskhub/stats
POST /taskhub/tasks
GET  /taskhub/tasks
GET  /taskhub/tasks/{task_id}
POST /taskhub/claim
POST /taskhub/tasks/{task_id}/complete
POST /taskhub/tasks/{task_id}/fail
POST /taskhub/tasks/{task_id}/retry
POST /taskhub/tasks/{task_id}/block
POST /taskhub/tasks/{task_id}/approve
POST /taskhub/tasks/{task_id}/cancel
```

`review.human` 批准行为：

- 默认只改变人工审核任务状态为 `succeeded`。
- 当 `input.approval_plan` 存在时，批准后按白名单创建下游任务。
- 当前允许派生：`ops.note`、`project.context.sync`、`project.git.status`、`quality.env.check`、`requirement.split`、`code.change`、`code.diff.preview`、`code.change.apply`、`test.run`。
- approve API 支持提交编辑后的 `approval_plan`，并继续按白名单校验。
- 当前图入口默认在人工批准后派生环境检测、代码变更计划、diff 预览和 `test.run api.lint` 质量门禁。

LangGraph 初级入口：

```text
POST /graphs/douyin_stage1_requirement_flow/invoke
```

该入口会为输入需求生成：

- `ops.note`
- `project.context.sync`
- `project.git.status`
- `requirement.split`
- `review.human`

其中前四类任务可由当前 worker 自动处理，`review.human` 保持人工门禁。

## 4. 已完成 Worker 能力

`worker-31-24` 已开启 TaskHub 轮询。

当前支持任务类型：

```text
echo
system_info
ops.note
project.context.sync
project.git.status
requirement.split
code.change
code.diff.preview
code.change.apply
test.run
quality.env.check
```

`.24` 已配置免密读取 `.31` 项目工作副本，并可通过 `.31` 执行项目质量命令。

上下文同步当前允许读取：

- `AGENTS.md`
- `PROJECT_STATE.md`
- `CONTEXT_PACK.md`
- `README.md`
- `package.json`
- `docs/langgraph/migration-three-stage-plan.md`
- `docs/langgraph/prd-stage1-taskhub.md`
- `docs/langgraph/prd-stage1-web-console.md`

## 5. 已完成 Web 控制台能力

页面：

- 总览
- 任务列表
- 任务详情
- 质量命令
- 代码变更
- 新建任务
- Worker 状态

已接入：

- 健康状态
- 任务统计
- 任务列表
- 任务详情
- 创建单个任务
- 交给 LangGraph 拆解
- retry / block / cancel 操作
- review.human 人工批准操作
- review.human 专用审批表单
- review.human 审批前下游计划展示
- review.human 审批前 `approval_plan` JSON 编辑
- review.human 审批原因输入
- review.human 批准后下游任务创建结果展示
- 质量命令页面
- 质量环境检测任务 `quality.env.check`
- 快捷创建 `test.run` 白名单质量任务
- 细粒度质量命令：`web.check`、`api.lint`、`api.typecheck`、`api.test`、`executor.check`
- 最近质量任务列表
- 质量任务运行结果摘要
- stdout / stderr / error 尾部结果展示
- 代码变更页面
- 快捷创建 `code.change` 计划任务
- 快捷创建 `code.diff.preview` diff 预览任务
- 快捷创建 `code.change.apply` dry-run 校验任务
- 最近代码任务列表
- 代码变更计划、diff 预览、dry-run 校验结果、候选路径、安全规则和工作副本状态展示
- worker 状态
- 敏感字段脱敏显示
- 任务详情摘要卡片
- 任务列表选中行高亮
- 创建单个任务后自动跳转并选中新任务
- 交给 LangGraph 拆解后自动跳转任务列表并选中新建任务
- 任务表格与 worker 表格基础 HTML 转义，避免标题/详情裸插入页面

## 6. 验证结果

已验证：

- Controller active。
- Worker active。
- Postgres health ok。
- Redis health ok。
- TaskHub 表自动初始化。
- 创建任务成功。
- `.24` worker 自动领取任务。
- `pending -> running -> succeeded` 成功。
- `running -> failed -> pending -> canceled` 成功。
- `running -> blocked -> canceled` 成功。
- `API_KEY` 字段返回时已脱敏。
- `/graphs/douyin_stage1_requirement_flow/invoke` 已验证可生成 5 个任务：`ops.note`、`project.context.sync`、`project.git.status`、`requirement.split`、`review.human`。
- `project.context.sync` 可从 `.24` 只读读取 `.31` 项目工作副本文件摘要。
- `project.git.status` 可读取 `.31` 工作副本 head 与 `git status --short`。
- `requirement.split` 可生成初级拆解 artifact。
- `review.human` 可通过 approve API 和 Web 按钮批准为 succeeded。
- `test.run` 支持白名单命令执行，非法命令会进入 failed。
- `.31` 已安装用户级 Node v26.8.1 / npm 11.19.0，`npm run bootstrap` 已完成。
- `test.run api.lint` 已通过 `.24` worker 触发 `.31` 工作副本命令，并到达真实 Ruff 检查。
- `.31` 工作副本已修复 3 个 Ruff 可修复问题：`products.py` 未使用导入，以及 `workbench.py`、`shops.py` import 排序。
- `test.run api.lint` 已通过 TaskHub 重试验证为 succeeded，输出 `All checks passed!`。
- 脱敏规则已修正：`API_KEY` 继续脱敏，`command_key` 不再被误脱敏。
- 完整图冒烟后队列状态已确认：`pending=0`、`running=0`。
- Web 控制台已完成初级 UX 增强，页面源码和 Controller 健康检查已验证。
- `review.human` 批准后自动派生下游任务已验证：批准任务 `618ab879-77d2-4d04-904d-cc5ad51a62af` 后创建 `test.run` 任务 `20675322-9502-478d-be13-99dcd1264ad5`。
- 下游 `test.run api.lint` 已由 `.24` worker 自动领取并执行成功，输出 `All checks passed!`。
- Web 质量命令页面已上线，页面源码和 Controller 健康检查已验证。
- 质量面板任务格式已验证：创建 `test.run api.lint` 任务 `48a4ff51-7006-406f-8b53-82486bd040d4`，由 `.24` worker 执行成功，输出 `All checks passed!`。
- `quality.env.check` 已上线，`.24` worker 可检测 `.31` 项目副本的 project root、git、Node、npm、Python、依赖目录和 npm scripts。
- 质量环境检测修正版任务 `2b008e9e-bf3e-48ef-9279-7ecfc70b1e97` 已执行成功，检测结果 `passed=true`。
- Worker 已修正长命令阻塞健康接口的问题：`test.run`、`project.context.sync`、`project.git.status`、`quality.env.check` 通过线程执行。
- `code.change` 初级计划模式已上线，`.24` worker 可领取并生成代码变更计划，但 `will_modify_files=false`，不实际修改项目文件。
- `code.change` 计划任务 `9f19af52-9f37-432d-8f80-3ea3613a5f76` 已执行成功，返回候选路径、执行步骤、安全规则和工作副本状态。
- `review.human` 审批白名单已允许 `quality.env.check` 和 `code.change`。
- 图入口审批后派生链已验证：批准 `40faa591-e8d1-4993-bb2a-5a1245c65967` 后创建环境检测、代码变更计划和 `api.lint` 三个下游任务。
- 审批后 `code.change` 下游任务 `66445bcf-3832-4e8b-831b-8bc0a65b1dea` 已执行成功，审批后 `test.run api.lint` 下游任务 `21df71d3-22e8-4de6-aa78-9d3f6257142f` 已执行成功。
- Web 任务详情已增加 `review.human` 专用审批表单，可在批准前查看需求、审批说明、`approval_plan` 下游任务、已创建下游任务，并填写审批原因后批准、阻塞或取消。
- 审批表单 smoke 任务 `63dc2c76-affa-4d93-a22d-82bdebefcf82` 已创建并保留 pending，供 Web 页面人工验证。
- Controller approve API 已支持提交编辑后的 `approval_plan`，并继续使用任务类型白名单校验。
- Web 审批表单已支持编辑 `approval_plan` JSON，批准时按编辑后的计划创建下游任务。
- 审批计划编辑 smoke 已验证：批准 `3c0ef1dc-b8ab-4f1a-a42c-b4a71bda0518` 时将原三步计划改为仅创建一个 `quality.env.check`，结果 `edited_approval_plan=true`。
- 编辑后下游任务 `dca3f328-7d13-4e13-bfe1-830c96a59d4b` 已由 `.24` worker 执行成功，检测结果 `passed=true`。
- `code.diff.preview` 已上线，可读取 `.31` 工作副本当前 `git diff`，并在 Web 代码页展示 diff 尾部内容。
- `code.diff.preview` smoke 任务 `8d42aa6e-113c-4b26-bb46-9d23cd4ef301` 已执行成功，结果 `will_modify_files=false`。
- `code.change.apply` 已上线，当前支持用户提供 unified diff 的 dry-run 校验；只有 `input.apply=true` 且 diff 路径在 `allowed_paths` 内时才会执行 `git apply`。
- `code.change.apply` 空 diff 拒绝保护已验证，任务 `f4b5836b-7c3c-4f73-905b-a782e96071b4` 返回 `MISSING_DIFF` 后已清理为 canceled。
- `code.change.apply` dry-run 正路径已验证，任务 `3a717f52-3406-41cf-bd44-f25346707315` 返回 `check_exit_code=0` 且 `will_modify_files=false`，确认未创建 smoke 文件。
- 质量命令已拆细，`web.check` smoke 任务 `9da69365-e54d-4eef-ac5f-ae2fbbbf5080` 已执行成功。
- 初级完整链路 smoke 已验证：批准 `53433a08-9a4c-4ad8-b498-7ad331041a78` 后创建并执行 `quality.env.check`、`code.change`、`code.diff.preview`、`test.run api.lint` 四个下游任务。
- 初级完整链路下游任务 `50e31346-4e12-43f0-b978-ecaff08665fb`、`b6fc6076-a2a0-415b-aaf4-3c7a6aeeb9c3`、`9ee045d9-c33c-446b-9310-78ec71aae6ec`、`462cc9b9-3630-43a1-9f6c-a8690a2c27ef` 已全部执行成功。
- 初级完成验收时队列已确认：`pending=0`、`running=0`。
- Web Provider Key 表单已支持 API key 掩码输入、默认 API Model 修改、按角色 Model 覆盖字段输入，并可清空覆盖回退到默认 API Model。
- Web Planner 页已增加 Proxy 配置表单，可配置启用状态、代理地址、`NO_PROXY`，并支持保存后触发 `langgraph-control.service` 用户级服务重启。
- Controller 已通过 `/planner/proxy/config` 读写代理配置，返回值仅显示代理掩码和开关状态；`.env` 权限保持 `0600`。
- `.31` 已验证直连 OpenAI 超时，走 `192.168.31.200:7893` 可访问 OpenAI API；控制服务已配置代理并重启生效。
- GPT API provider 测试已修正 `gpt-5*` 模型参数兼容：使用 `max_completion_tokens`，并不发送非默认 `temperature`。
- `gpt_api` Provider 连通性测试已通过，测试模型为 `gpt-5.6-terra`。
- Provider JSON 解析已增强：支持剥离 `<think>...</think>` 和 ```json fenced code block，避免 DeepSeek/MiniMax 返回推理或代码块包装时误判为 invalid response。
- Provider 测试 token 上限已提高，避免 MiniMax 在测试响应中途截断导致 JSON 解析失败。
- 三个 API provider 单测已通过：`gpt_api/planner` 使用 `gpt-5.6-terra`，`deepseek_api/reviewer` 使用 `deepseek-v4-pro`，`minimax_api/risk` 使用 `MiniMax-M3`。
- 五角色 LLM Planner 冒烟已通过，执行顺序为 `planner -> coder -> reviewer -> risk -> supervisor`；API provider 均成功返回角色输出，Plus/Pro 账号型 provider 仍按预期提示尚未接入账号会话。
- 五角色冒烟创建任务：`ops.note`、`project.context.sync`、`project.git.status` 已由 `.24` worker 执行成功；`review.human` 任务 `8edf1ce1-c5fc-4960-946c-cde264d37024` 保持 pending，供 Web 人工审批。
- GPT 角色 fallback 链路已固化为 `ChatGPT Plus 账号 -> ChatGPT Pro 账号 -> GPT API`，适用于 `planner`、`coder`、`supervisor` 三个角色。
- Web Planner 页已增加账号 Fallback 配置，可设置 Plus/Pro 的连接状态与额度状态；`quota_exceeded` 会产生额度用尽提醒并继续 fallback 到下一个 provider。
- fallback smoke 已验证：Plus 标记 `quota_exceeded`、Pro 标记 `not_connected` 时，`planner`、`coder`、`supervisor` 均按顺序记录提醒并最终由 `gpt_api` 成功接管。

## 7. 已知边界

- LLM planner 已具备多角色配置、角色级 API model 映射、安全配置检查、本地交互式 provider 配置脚本、Web key 掩码配置、Web 代理配置、Web Plus/Pro 账号状态配置、API provider 调用、provider 连通性测试、fallback 尝试记录和额度/鉴权/限流提醒；但 Pro/Plus 账号型 provider 目前仍是状态接入骨架，尚未实现真实账号会话自动调用。
- `review.human` 当前支持专用审批表单、`approval_plan` 展示和 JSON 编辑，但尚未支持表格式多步骤编辑。
- Web 控制台是单文件初级版，不含登录权限；本轮验证采用源码语法检查、页面内容检查和后端 API 检查。
- `code.change` 当前只做计划，不直接写业务文件。
- `code.change.apply` 已具备受控 dry-run 和显式 apply 骨架，但初级阶段不建议默认自动派生真实写入任务。
- `npm run check` 在本项目当前环境下超过 300 秒超时，任务 `10e46fc1-0923-4d81-9808-3f883c2185cc` 已被标记 failed；后续需要拆成更细粒度命令或延长超时。
- 当前不执行生产部署，不自动发布抖店商品。
- 当前不直接修改 `.17` 项目仓库代码。
- `.17` 主机缺少 `git` 和 `npm` 命令；因此 LangGraph 初级阶段改为在 `.31` 工作副本执行 `git status` 和 `npm run ...` 质量入口。
- `.31` 工作副本继承了 `.17` 的既有 dirty/untracked 状态；本次新增业务代码 diff 仅限 3 个 `api.lint` 修复文件。
- `.31` 工作副本中的 LangGraph 文档位于 `docs/langgraph/`，当前仍未提交。

## 8. 下一步建议

优先级顺序：

1. 进入中级阶段：接入 LLM planner，把规则拆解升级为模型拆解，但状态仍由 TaskHub 控制。
2. 增加 `review.human` 表格式多步骤执行计划编辑。
3. 增加 `code.change.apply` 的人工批准后真实业务 diff 执行流程和回滚记录。
4. 增加登录权限、操作审计和按项目隔离的 Web 控制台。
