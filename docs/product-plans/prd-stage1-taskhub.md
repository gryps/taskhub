# PRD：LangGraph 初级阶段 TaskHub

> 适用说明：本文保留为一期历史实施基线。初级收口标准、控制面/执行面边界、任务 lease、幂等、认证和 Git 权威要求以 `docs/langgraph/prd-three-stage-v2.md` 为准。

## 1. 产品名称

LangGraph TaskHub 初级版。

## 2. 项目背景

当前项目位于：

```text
192.168.31.17:/home/gryps/.openclaw/workspace/douyin-listing-workbench
```

本次 LangGraph 继续开发的运行架构固定为：

```text
gryps@192.168.31.31
  LangGraph Controller
  TaskHub
  Postgres
  Redis
  API

gryps@192.168.31.24
  Worker
  测试执行
  工具执行
  浏览器自动化
```

`192.168.31.17` 只保存原项目 Git 仓库和 OpenClaw 工作区，不作为 LangGraph 运行节点。

该项目原本依赖 OpenClaw 多 agent PW 模式推进。现有项目已经包含前端、后端、执行器、任务单、发布证据、上下文包和项目状态文件。

历史协作中，模型可以参与开发和验收，但存在任务状态不够可靠、执行中断难恢复、监督 agent 不能准确掌握真实执行情况的问题。

本 PRD 定义 LangGraph 初级阶段要开发的 TaskHub。它不是完整替代 OpenClaw，而是先为后续 LangGraph 接管项目开发流程提供任务交换中心。

## 3. 产品目标

建立一个最小可用、可恢复、可观察的任务中心。

核心目标：

- 所有 agent 任务都有持久记录。
- worker 通过统一接口领取任务。
- 任务执行结果和失败原因必须回写。
- 项目 owner 可以查看、重试、阻塞、取消任务。
- LangGraph 可以基于任务状态继续推进流程。

一句话目标：

把“聊天里的协作”变成“数据库里的任务状态机”。

## 4. 用户角色

| 角色 | 说明 |
| --- | --- |
| 项目 owner | 创建需求、查看进度、人工验收、批准高风险动作 |
| supervisor graph | LangGraph 中的监督流程，负责拆任务、看状态、决定下一步 |
| worker | 领取任务并执行，例如测试、工具调用、代码检查、上下文同步 |
| model agent | 被 worker 或 controller 调用的模型能力 |
| operator | 查看服务、日志、失败任务和系统健康 |

## 5. 范围

### 5.1 包含

- TaskHub 数据表。
- 任务创建 API。
- 任务列表 API。
- 任务详情 API。
- 任务领取 API。
- 任务完成 API。
- 任务失败 API。
- 任务重试 API。
- 任务阻塞 API。
- 任务取消 API。
- worker 健康和心跳。
- 最小 LangGraph 工作流。
- 简单控制台或 API-first 页面。
- `.17` 项目上下文只读同步任务。

### 5.2 不包含

- 完整替代 OpenClaw。
- 自动发布抖店商品。
- 自动生产部署。
- 完整浏览器自动化验收。
- 多项目统一后台。
- 成本统计和额度管理。
- 高级权限系统。
- 复杂 UI。

## 6. 成功标准

初级版完成时，必须满足：

- 可以创建任务，状态为 `pending`。
- worker 可以原子领取任务，状态变为 `running`。
- worker 可以提交成功结果，状态变为 `succeeded`。
- worker 可以提交失败原因，状态变为 `failed`。
- failed 任务可以重试回 `pending`。
- blocked 任务可以清晰显示人工介入原因。
- 任意任务详情能看到输入、结果、错误、worker、时间和状态历史。
- worker 重启不会丢失已有任务状态。
- 不暴露 API Key、Cookie、密码、抖店登录态。

## 7. 核心状态机

任务状态：

```text
pending
running
succeeded
failed
blocked
canceled
```

允许流转：

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

规则：

- 不允许 `succeeded -> running`。
- 不允许 `canceled -> pending`。
- `running` 任务只能由领取它的 worker 完成或失败。
- 管理员 override 必须记录原因。
- 每次状态变化必须写入历史。

## 8. 任务字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | 是 | 任务 ID |
| `project` | string | 是 | 初期固定 `douyin-listing-workbench` |
| `type` | string | 是 | 任务类型 |
| `title` | string | 是 | 任务标题 |
| `state` | string | 是 | 当前状态 |
| `priority` | int | 是 | 优先级，数字越大越优先 |
| `input` | JSON | 是 | 任务输入 |
| `metadata` | JSON | 否 | 文件、分支、模型偏好等 |
| `result` | JSON | 否 | 成功结果 |
| `error` | JSON | 否 | 失败原因 |
| `worker_id` | string | 否 | 当前领取 worker |
| `retry_count` | int | 是 | 重试次数 |
| `created_at` | timestamp | 是 | 创建时间 |
| `updated_at` | timestamp | 是 | 更新时间 |
| `claimed_at` | timestamp | 否 | 领取时间 |
| `completed_at` | timestamp | 否 | 完成时间 |

历史表字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 历史记录 ID |
| `task_id` | UUID | 任务 ID |
| `from_state` | string | 原状态 |
| `to_state` | string | 新状态 |
| `actor` | string | 操作者 |
| `reason` | string | 原因 |
| `payload` | JSON | 附加数据 |
| `created_at` | timestamp | 记录时间 |

## 9. API 设计

Controller 地址：

```text
http://192.168.31.31:8123
```

### 9.1 创建任务

```text
POST /taskhub/tasks
```

请求：

```json
{
  "project": "douyin-listing-workbench",
  "type": "requirement.split",
  "title": "拆解商品编辑页优化需求",
  "priority": 50,
  "input": {
    "requirement": "优化手机端商品编辑页"
  },
  "metadata": {
    "source": "manual"
  }
}
```

返回：

```json
{
  "id": "uuid",
  "state": "pending"
}
```

### 9.2 查询任务列表

```text
GET /taskhub/tasks?state=pending&project=douyin-listing-workbench&limit=50
```

返回任务摘要列表。

### 9.3 查询任务详情

```text
GET /taskhub/tasks/{task_id}
```

返回任务完整字段和状态历史。

### 9.4 领取任务

```text
POST /taskhub/claim
```

请求：

```json
{
  "worker_id": "worker-31-24",
  "project": "douyin-listing-workbench",
  "types": ["test.run", "project.context.sync", "ops.note"]
}
```

返回：

```json
{
  "task": {
    "id": "uuid",
    "type": "test.run",
    "input": {}
  }
}
```

无任务时：

```json
{
  "task": null
}
```

### 9.5 完成任务

```text
POST /taskhub/tasks/{task_id}/complete
```

请求：

```json
{
  "worker_id": "worker-31-24",
  "result": {
    "summary": "测试通过",
    "artifacts": []
  }
}
```

### 9.6 失败任务

```text
POST /taskhub/tasks/{task_id}/fail
```

请求：

```json
{
  "worker_id": "worker-31-24",
  "error": {
    "code": "COMMAND_FAILED",
    "message": "npm run check failed"
  }
}
```

### 9.7 重试任务

```text
POST /taskhub/tasks/{task_id}/retry
```

请求：

```json
{
  "actor": "owner",
  "reason": "依赖已修复，重新执行"
}
```

### 9.8 阻塞任务

```text
POST /taskhub/tasks/{task_id}/block
```

请求：

```json
{
  "actor": "supervisor",
  "reason": "需要用户确认商品发布边界"
}
```

### 9.9 取消任务

```text
POST /taskhub/tasks/{task_id}/cancel
```

请求：

```json
{
  "actor": "owner",
  "reason": "需求取消"
}
```

## 10. 初级 LangGraph 工作流

工作流名称：

```text
douyin_stage1_requirement_flow
```

节点：

| 节点 | 职责 |
| --- | --- |
| `receive_requirement` | 接收需求 |
| `load_project_context` | 读取项目上下文摘要，不读取敏感信息 |
| `plan_tasks` | 拆成任务 |
| `create_taskhub_tasks` | 写入 TaskHub |
| `summarize_status` | 返回当前任务状态 |

图结构：

```text
START
  -> receive_requirement
  -> load_project_context
  -> plan_tasks
  -> create_taskhub_tasks
  -> summarize_status
  -> END
```

阶段一先不要求自动代码修改。任务可以先停在待领取或由 worker 执行安全工具。

## 11. Worker 要求

第一批 worker：

- `worker-31-main`
- `worker-31-24`

worker 行为：

- 定时请求 `/taskhub/claim`。
- 只领取自己支持的任务类型。
- 执行完成后调用 `/complete`。
- 执行失败后调用 `/fail`。
- 每次执行记录命令、退出码、摘要和 artifact 路径。
- 输出必须脱敏。

初始支持任务类型：

- `project.context.sync`
- `test.run`
- `ops.note`
- `review.model`

后续支持任务类型：

- `code.change`
- `h5.inspect`
- `openclaw.adapter.call`

## 12. 项目上下文同步

TaskHub 需要知道 `.17` 项目的基本上下文，但不能把整个仓库塞进任务。

阶段一只同步：

- `PROJECT_STATE.md`
- `CONTEXT_PACK.md`
- `README.md`
- `AGENTS.md`
- `package.json`
- `docs/` 中指定 PRD/ADR 文档
- `tasks/` 中指定活动任务单

同步结果应保存为摘要 artifact，而不是替代源文件。

## 13. 控制台需求

初级版控制台可以很简单。

必须有：

- 任务列表。
- 任务详情。
- 创建任务。
- 重试任务。
- 阻塞任务。
- 取消任务。
- worker 状态。

任务列表默认排序：

1. `blocked`
2. `failed`
3. `running`
4. `pending`
5. `succeeded`
6. `canceled`

## 14. 安全要求

- 不显示完整密钥、Cookie、Token、密码。
- 不记录抖店登录态。
- 不提交真实商品图或个人信息。
- 不允许 worker 直连生产数据库。
- 不允许默认执行发布商品动作。
- 涉及生产部署、数据迁移、远程覆盖文件，必须进入 `review.human`。

## 15. 初级版验收用例

### 用例 1：创建任务

输入一个 `ops.note` 任务。

期望：

- 返回任务 ID。
- 状态为 `pending`。
- 列表可见。

### 用例 2：领取任务

worker 请求领取 `ops.note`。

期望：

- 任务变为 `running`。
- 记录 worker ID。
- 其他 worker 不能重复领取。

### 用例 3：完成任务

worker 提交 result。

期望：

- 状态变为 `succeeded`。
- result 可在详情查看。
- 历史记录包含 `running -> succeeded`。

### 用例 4：失败后重试

worker 提交 error。

期望：

- 状态变为 `failed`。
- error 可见。
- 调用 retry 后状态回到 `pending`。
- retry_count 增加。

### 用例 5：阻塞任务

supervisor 提交 block reason。

期望：

- 状态变为 `blocked`。
- 默认列表靠前显示。
- 必须有阻塞原因。

### 用例 6：敏感信息脱敏

任务 result 包含类似 `API_KEY` 或 `PASSWORD` 字段。

期望：

- API 返回和 UI 展示中被脱敏。
- 原始秘密不得写入普通日志。

## 16. 开发里程碑

### M1：TaskHub 数据和基础 API

- 数据表。
- 创建任务。
- 列表。
- 详情。

### M2：状态流转 API

- claim。
- complete。
- fail。
- retry。
- block。
- cancel。
- 历史记录。

### M3：worker 轮询

- `.24` worker 领取任务。
- 支持 `ops.note` 和 `project.context.sync`。
- 支持失败回写。

### M4：控制台

- 任务列表。
- 任务详情。
- 操作按钮。
- worker 状态。

### M5：LangGraph 接入

- 需求输入。
- 拆任务。
- 创建 TaskHub 任务。
- 汇总任务状态。

## 17. 初级版交付边界

完成初级版后，系统应该可以承接原 OpenClaw 项目的新需求，但只承接到“任务化、可见、可恢复”的层面。

不承诺：

- 全自动完成开发。
- 全自动验收。
- 全自动发布。
- 完全替代 OpenClaw。

承诺：

- 任务不再只存在聊天里。
- agent 中断后有恢复点。
- 人能看见真实状态。
- 后续二次开发有稳定底座。
