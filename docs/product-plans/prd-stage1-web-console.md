# PRD：LangGraph 初级阶段 Web 控制台

> 适用说明：本文保留为一期历史实施基线。当前 Web 已包含部分中级 Provider 配置能力；涉及登录授权、账号真实状态、服务重启审计和后续页面结构时，以 `docs/langgraph/prd-three-stage-v2.md` 为准。

## 1. 产品定位

LangGraph 初级 Web 控制台是给项目 owner 使用的任务管理和流程观察入口。

它服务于当前项目：

```text
192.168.31.17:/home/gryps/.openclaw/workspace/douyin-listing-workbench
```

运行架构固定为：

```text
gryps@192.168.31.31
  LangGraph Controller
  TaskHub
  Postgres
  Redis
  Web/API

gryps@192.168.31.24
  Worker
  测试执行
  工具执行
  浏览器自动化
```

`192.168.31.17` 只作为原项目 Git 仓库和 OpenClaw 工作区，不作为 LangGraph 运行节点。

## 2. 背景问题

以前使用 OpenClaw 多 agent PW 模式时，任务推进主要依赖聊天、Workboard、上下文包和人工观察。问题是：

- 不容易一眼知道哪个任务正在做、谁在做、为什么卡住。
- agent 中途断线后，需要人工回忆上下文并补救。
- 模型监督无法稳定看到真实任务状态。
- 测试、验收、失败原因和重试记录没有统一入口。

初级 Web 控制台要解决的是使用入口问题：让用户不用直接调 API，也能创建任务、看状态、重试失败任务、阻塞任务、查看 worker 和 LangGraph 运行情况。

## 3. 产品目标

阶段一目标：

- 提供一个可用的 LangGraph 使用入口。
- 让 TaskHub 的任务状态可视化。
- 让用户能手动创建、重试、阻塞、取消任务。
- 让 worker 状态和任务执行结果可见。
- 支持把原 OpenClaw 项目需求继续输入到 LangGraph 流程。

阶段一不追求复杂美观，不追求完整多 agent 自治，先保证可靠、清楚、可操作。

## 4. 用户角色

| 角色 | 说明 |
| --- | --- |
| 项目 owner | 主要使用者，创建需求、查看进度、验收或人工介入 |
| supervisor graph | LangGraph 流程，负责拆任务、汇总状态、判断下一步 |
| worker | `.24` 或 `.31` 上的执行节点 |
| 开发/运维 | 查看失败、日志、worker 健康和接口状态 |

## 5. 初级版页面范围

必须开发 5 个页面或视图：

1. 总览页
2. 任务列表页
3. 任务详情页
4. 新建任务页
5. Worker 状态页

可以放在一个单页应用内，用左侧导航或顶部标签切换。

## 6. 页面一：总览页

### 6.1 目标

让用户打开 Web 后，马上知道 LangGraph 当前是否可用、任务是否堆积、哪里需要处理。

### 6.2 展示内容

必须展示：

- Controller 健康状态。
- Postgres 状态。
- Redis 状态。
- 在线 worker 数。
- `pending` 任务数量。
- `running` 任务数量。
- `failed` 任务数量。
- `blocked` 任务数量。
- 最近 10 条任务动态。

### 6.3 交互

必须支持：

- 点击状态卡片跳转到对应过滤后的任务列表。
- 点击最近任务进入任务详情。
- 手动刷新。

### 6.4 接口

使用：

```text
GET /health
GET /workers
GET /taskhub/tasks?limit=10
GET /taskhub/stats
```

如果初期没有 `/taskhub/stats`，前端可以先用任务列表聚合，但后端最终应提供统计接口。

## 7. 页面二：任务列表页

### 7.1 目标

让用户查看所有任务，并快速找到失败、阻塞、运行中的任务。

### 7.2 列表字段

必须展示：

- 短 ID。
- 标题。
- 项目。
- 类型。
- 状态。
- 优先级。
- worker。
- 重试次数。
- 更新时间。

### 7.3 筛选

必须支持：

- 按状态筛选。
- 按任务类型筛选。
- 按 worker 筛选。
- 按项目筛选，初期默认 `douyin-listing-workbench`。

### 7.4 排序

默认排序：

```text
blocked
failed
running
pending
succeeded
canceled
```

同状态内按 `updated_at` 倒序。

### 7.5 行操作

根据状态显示操作：

- `failed`：重试、取消。
- `blocked`：解除阻塞、取消。
- `pending`：取消。
- `running`：查看详情，初期不强制支持取消运行中任务。
- `succeeded`：查看详情。
- `canceled`：查看详情。

## 8. 页面三：任务详情页

### 8.1 目标

让用户看到一个任务的完整上下文和执行证据。

### 8.2 展示内容

必须展示：

- 任务 ID。
- 标题。
- 项目。
- 类型。
- 当前状态。
- 优先级。
- worker。
- 创建时间。
- 更新时间。
- 领取时间。
- 完成时间。
- 输入 JSON。
- metadata JSON。
- result JSON。
- error JSON。
- 状态历史。

### 8.3 状态历史

状态历史必须按时间倒序展示：

- 原状态。
- 新状态。
- 操作者。
- 原因。
- 时间。

### 8.4 操作

必须支持：

- 重试失败任务。
- 阻塞运行中或失败任务。
- 取消 pending、failed、blocked 任务。
- 复制任务 ID。
- 复制 result/error 摘要。

### 8.5 安全显示

前端不得展示完整敏感字段值。

字段名包含以下关键词时必须脱敏：

```text
KEY
TOKEN
SECRET
PASSWORD
COOKIE
AUTH
SESSION
```

## 9. 页面四：新建任务页

### 9.1 目标

让用户可以把一个需求或操作意图提交给 LangGraph/TaskHub。

### 9.2 表单字段

必须包含：

- 项目：默认 `douyin-listing-workbench`。
- 任务类型。
- 标题。
- 优先级。
- 需求/输入内容。
- metadata，可选。

任务类型初期提供：

```text
requirement.split
project.context.sync
code.change
test.run
h5.inspect
review.model
review.human
ops.note
```

### 9.3 两种创建模式

必须支持：

1. 直接创建 TaskHub 任务。
2. 通过 LangGraph 提交需求，由 LangGraph 拆解后创建任务。

对应按钮：

- 创建单个任务。
- 交给 LangGraph 拆解。

### 9.4 接口

使用：

```text
POST /taskhub/tasks
POST /graphs/douyin_stage1_requirement_flow/invoke
```

如果后端初期还没有 `/graphs/.../invoke`，可先用现有 `/invoke`，但 UI 文案要保持“交给 LangGraph 拆解”。

## 10. 页面五：Worker 状态页

### 10.1 目标

让用户知道 `.31` 和 `.24` worker 是否在线、能做什么、最近是否报错。

### 10.2 展示内容

必须展示：

- worker ID。
- host。
- 在线状态。
- 支持任务类型。
- 最近心跳时间。
- 当前运行任务。
- 最近成功任务。
- 最近失败任务。

### 10.3 接口

使用：

```text
GET /workers
GET /taskhub/tasks?state=running
GET /taskhub/tasks?state=failed&limit=10
```

## 11. 初级版功能优先级

P0 必须做：

- 总览页。
- 任务列表。
- 任务详情。
- 新建任务。
- 重试失败任务。
- worker 状态。
- 敏感信息脱敏。

P1 可以随后做：

- 状态数量统计。
- 任务搜索。
- JSON 展开/折叠。
- 解除阻塞。
- 手动刷新间隔设置。

P2 暂不做：

- 用户登录权限。
- 多项目空间。
- 成本统计。
- 模型额度面板。
- 复杂图形化 LangGraph 节点编辑器。
- 自动部署按钮。

## 12. 交互原则

- 页面要偏工具型，不做营销式首页。
- 打开第一屏就是任务和系统状态。
- 失败和阻塞任务必须醒目。
- 所有危险操作都要二次确认。
- 不用复杂动画。
- 手机和桌面都要能用，优先桌面管理效率，同时保证手机可读。

## 13. API 依赖

前端依赖这些后端能力：

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
POST /taskhub/tasks/{task_id}/cancel
POST /graphs/douyin_stage1_requirement_flow/invoke
```

后端可按里程碑逐步实现。前端要对未实现接口显示明确错误，不要空白。

## 14. 数据状态展示规范

状态颜色建议：

| 状态 | 含义 | 展示倾向 |
| --- | --- | --- |
| `pending` | 等待领取 | 中性 |
| `running` | 正在执行 | 蓝色或强调 |
| `succeeded` | 成功 | 绿色 |
| `failed` | 失败 | 红色 |
| `blocked` | 需人工处理 | 橙色或高亮 |
| `canceled` | 已取消 | 灰色 |

列表中 `blocked` 和 `failed` 不得被隐藏在成功任务后面。

## 15. 安全边界

初级 Web 控制台不得提供：

- 生产部署按钮。
- 自动发布商品按钮。
- 直接编辑 `.17` 仓库文件的功能。
- 直接查看密钥、Cookie、Token、登录态的功能。
- 直接连接生产数据库的入口。

涉及生产、抖店登录态、真实商品素材、客户数据的能力，必须等中级阶段加入权限和审批后再做。

## 16. 验收用例

### 用例 1：打开总览页

期望：

- 能看到 Controller、Postgres、Redis、worker 状态。
- 能看到 pending、running、failed、blocked 数量。

### 用例 2：创建 ops.note 任务

期望：

- 表单提交成功。
- 任务出现在列表。
- 状态为 `pending`。

### 用例 3：worker 自动执行任务

期望：

- 任务从 `pending` 变成 `running`。
- 完成后变成 `succeeded`。
- 详情页能看到 result。

### 用例 4：失败任务重试

期望：

- failed 任务在列表靠前显示。
- 点击重试后状态回到 `pending`。
- retry_count 增加。
- 历史记录保留上次失败原因。

### 用例 5：阻塞任务

期望：

- 可以填写阻塞原因。
- 状态变为 `blocked`。
- 总览页显示 blocked 数量。

### 用例 6：敏感字段脱敏

期望：

- result 或 error 中出现 `TOKEN`、`PASSWORD` 等字段名时，前端不展示原值。

## 17. 开发里程碑

M1：静态控制台骨架

- 导航。
- 总览页。
- 任务列表页。
- 任务详情页空状态。

M2：TaskHub API 接入

- 创建任务。
- 列表。
- 详情。
- 重试。
- 阻塞。
- 取消。

M3：worker 状态接入

- worker 列表。
- 当前运行任务。
- 最近失败任务。

M4：LangGraph 入口

- 新建任务页增加“交给 LangGraph 拆解”。
- 展示拆解后创建的任务。

M5：可用性打磨

- 错误提示。
- loading 状态。
- 空状态。
- 手动刷新。
- 敏感信息脱敏。

## 18. 阶段一完成定义

当用户可以通过 Web 完成以下闭环时，初级 Web 控制台视为完成：

```text
打开总览
  -> 创建需求或任务
  -> 查看任务进入 pending
  -> worker 领取并执行
  -> 查看成功或失败结果
  -> 对失败任务重试或阻塞
  -> 通过 LangGraph 入口创建拆解任务
```

完成后，原 OpenClaw 项目就可以开始用 LangGraph 继续推进新需求，但仍保留人工审批和 OpenClaw 历史资料。
