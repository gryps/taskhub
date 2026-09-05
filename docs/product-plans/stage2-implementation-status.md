# LangGraph Stage 2 Implementation Status

更新时间：2026-09-04

当前阶段：中级阶段基础能力完成，业务计划仍需人工审批后执行。

## 1. Plus/Pro 真实推理与自动回切

状态：完成并通过真实推理验收。

- `.31` 使用独立的 Plus/Pro Codex Home，通过设备认证接入账号，不向账号 Runner 注入 API key。
- Plus、Pro Provider 均已返回符合严格 JSON Schema 的真实结构化结果。
- 规划、施工、监督角色支持 `Plus -> Pro -> GPT API` 独立 fallback 配置。
- 暂时性故障触发 60 秒冷却；冷却结束后优先探测原首选 Provider，恢复成功则当前请求直接回切首选模型。
- 登录失效、额度耗尽、限流、超时和网络错误均生成结构化原因与提醒。
- OpenAI 账号/API 强制使用 `OPENAI_PROXY_URL`；DeepSeek、MiniMax 显式直连；`.24/.31/.34` 控制流通过 `NO_PROXY` 直连局域网。

登录命令：

```bash
ssh -t gryps@192.168.31.31 '/home/gryps/apps/langgraph-control/scripts/codex_account_login.sh plus'
ssh -t gryps@192.168.31.31 '/home/gryps/apps/langgraph-control/scripts/codex_account_login.sh pro'
```

设备认证页面：`https://auth.openai.com/codex/device`。

## 2. 五角色证据链

状态：完成并通过真实多模型调用验收。

- 固定链路为规划 `planner`、施工 `coder`、审查 `reviewer`、风险 `risk`、监督 `supervisor`。
- 每个角色保存脱敏输入、输出、Provider、模型、状态、input/output hash 和 chain hash。
- 角色的 input hash 必须等于上一个角色的 chain hash，形成可验证的连续证据链。
- 真实验收工作流：`ef97047e-2c5d-4791-8f56-14b6dc090cfb`，5 个角色全部成功。
- 监督结论不会自动执行生产动作，生成 `review.human` 人工门禁。

## 3. Task 依赖 DAG

状态：完成并通过合同测试。

- Task 支持 `depends_on`，详情返回上游与下游关系。
- Worker 只能领取全部上游均成功的任务。
- 上游失败、阻塞或取消时，未执行的后继任务自动进入 `blocked`。
- 禁止自依赖、跨项目依赖和环路。
- 人工批准后生成的计划默认串行，也支持显式 `depends_on_indices`。

## 4. Linux 质量节点 `.24`

状态：独立执行环境和真实质量执行完成；当前业务 WIP 存在 API 回归，未伪报全绿。

- 裸仓库：`/home/gryps/repos/douyin-listing-workbench.git`。
- 独立 worktree：`/home/gryps/worktrees/douyin-listing-workbench-quality`。
- 分支：`taskhub/quality-31-24`，基线 `322dbc1d700ab602ed76c89000352374199c8298`。
- Worker：`worker-31-24-quality`，本地执行项目上下文、Git 状态、环境检测和白名单质量命令。
- `CODE_APPLY_ENABLED=false`，禁止该质量节点直接应用代码变更。
- 任务结果归档到 `/home/gryps/artifacts/langgraph`，生成 SHA-256 manifest。
- 全量门禁按 Web、API lint、API typecheck、API test、Executor 拆成顺序 DAG，每步独立归档。
- `PROJECT_TEST_TIMEOUT=3600`，适配低配置节点的完整 API 测试时长并保留任务心跳。
- Web build/TypeScript/ESLint、API Ruff、API mypy 均通过并归档。
- Executor Ruff、mypy（27 个源码文件）和 151 项 pytest 通过；任务 `e86fca00-aeeb-43b8-9750-419e3724d96e`。
- API 全量 pytest 约运行 24 分钟后检出 11 项失败，集中在当前 WIP 新增 `shop_id` 后旧接口与迁移夹具未适配；任务 `51df247f-7479-4a04-b901-d0eecc013b13`。
- 该结果属于业务候选代码的质量阻断，不是 Worker 部署失败；后继任务按 DAG 自动进入 `blocked`。
- 失败任务同样归档脱敏错误结果与 SHA-256 manifest（此能力自本轮收尾后生效）。

## 5. Windows GUI/H5 节点 `.34`

状态：完成并通过 TaskHub 真实有头浏览器验收。

- Worker：`worker-31-34-gui`，仅接受 `h5.inspect`。
- 使用系统 Edge 和 Playwright Core，在交互式 Windows 会话中执行 headed 验收。
- 真实任务：`4d5fa0d3-bc4a-4fc7-bf2b-38a31b630135`。
- `https://e.grypszhang.com` 桌面 `1440x1000`、移动端 `390x844` 均通过，无页面错误和横向溢出。
- 截图、请求和报告归档在 `C:\Users\user\langgraph-gui-worker\artifacts\<task-id>`，共 4 个带 SHA-256 的文件。
- 已知非阻断告警：站点 CSP 拦截 Google Fonts 外部样式；后续前端发布应改为自托管字体或调整 CSP。

## 6. Web 工作台与人工边界

状态：完成。

- 菜单为：总览、任务中心、规划编排、开发实施、审查验收、风险报告、监督裁决、模型调度、系统设置。
- 模型调度显示 Provider 健康、冷却和恢复回切状态。
- 任务中心显示 DAG 上下游；监督裁决显示五角色证据与哈希。
- 审查验收可创建 Windows H5 桌面/移动端任务并查看产物摘要。
- 模型生成计划只到人工审批门禁；批准前不得修改业务仓库、数据库、部署或生产数据。

## 运行节点

| 节点 | 职责 |
| --- | --- |
| `192.168.31.31:8123` | LangGraph 总控、TaskHub、模型编排、人工审批 |
| `192.168.31.24:8124` | Linux Git worktree、测试和质量门禁 |
| `192.168.31.34:8125` | Windows GUI、Edge/Playwright H5 验收和截图归档 |
