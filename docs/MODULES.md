# TaskHub V2 模块职责

本文补充 `docs/ARCHITECTURE.md`，记录稳定模块、公共接口和数据所有权。模块之间通过公开
服务、协议或领域模型协作，不得穿透访问内部实现。

| 模块 | 职责 | 拥有的数据 |
| --- | --- | --- |
| `domain.governance` | 规则、冻结绑定和受控例外的不可变模型 | `EngineeringPolicy`、`PolicyBinding`、`PolicyException` |
| `services.engineering_governance` | 规则生命周期、合同编译、绑定校验和例外审批 | 规则版本及项目例外的业务操作 |
| `services.engineering_policy_catalog` | 首次部署的内置全局规则目录 | 内置 v1 规则定义 |
| `services.governance_gate` | 对仓库和项目合同执行可重复的规则检查 | 无持久状态，只生成门禁发现项 |
| `services.project_contracts` | 项目边界、命令和规则绑定的项目级权威 | `ProjectContract` |
| `services.repository_contract_inference` | 将内置档案映射为仓库真实源码根和文档边界 | 无持久状态；输出合同模块候选 |
| `services.dag_task_scope` | 按任务意图解析验证属性和合同内可写模块 | 无持久状态；输出冻结任务作用域 |
| `services.dag_plan` | 将规格、合同及规则快照编译为计划与任务 | `ExecutionPlan`、`ProductionTask` |
| `services.dag_scheduler` | 按依赖调度批次并在集成后执行治理门禁 | `ExecutionBatch`、`TaskAttempt` |
| `workers.dag_executor` | 为编码节点组装含安装与质量命令的冻结上下文并执行批次验证 | 工作区结果，不拥有规则 |

## 平台核心模块

| 模块 | 职责 | 公共边界 / 数据所有权 |
| --- | --- | --- |
| `api` | HTTP/SSE、认证授权、输入输出适配与静态资源交付 | 路由契约；不拥有流程状态 |
| `security.agent_access` | 开发代理配对、凭据摘要、到期与吊销 | `agent-access.json`；不拥有用户密码或浏览器会话 |
| `workflows` | 主流程与实施子图、阶段转换、中断和恢复 | LangGraph checkpoint 中的流程位置与时间线 |
| `services.runs` | 启动、读取、恢复和归档运行 | 通过 graph thread 操作权威状态 |
| `services.project_preflight` | 聚合项目开工条件并返回可修复诊断 | 预检报告；不复制下游数据 |
| `services.project_activation` | 编排仓库接入后的契约、质量、验收与预检步骤 | 组合读模型；不持久化第二份就绪状态 |
| `domain` | 稳定领域记录、枚举、状态和策略契约 | 领域 schema 与校验规则 |
| `persistence` | checkpoint 和生产记录的持久化实现 | PostgreSQL/内存存储适配器 |
| `providers` | 模型调用、路由、健康和回退适配 | 模型结果与独立运行健康状态 |
| `workers` | 编码、验收、发布等受控执行能力 | 结构化执行结果与证据 |
| `execution` | 节点清单、安装/质量能力匹配、槽位、任务和运行器 | 节点与调度运行事实 |
| `node_agent` | 节点鉴权、工作区、契约安装和隔离质量命令执行 | 幂等节点任务结果 |
| `services.local_node_lifecycle` | Seed 本机节点创建、凭据轮换、原位镜像升级、健康确认与失败回滚 | 容器生命周期；节点数据卷和调度身份保持独立 |
| `services.external_windows_nodes` | Windows 实测机指纹确认、一次性 SSH 部署、健康确认与注册 | 外部节点元数据；不持久化 SSH 私钥 |
| `projects` | 权威仓库创建、连接验证与项目登记 | 项目注册与仓库位置 |
| `deployment` | 预生产部署与身份验证 | 部署结果，不拥有项目业务状态 |

## 前端模块

| 模块 | 职责 | 约束 |
| --- | --- | --- |
| `api/static/index.html` | 原生控制台语义壳层和稳定 DOM ID | 不承载业务计算 |
| `api/static/app.js` | 原生壳层、认证与开发流程装配 | 历史大文件，只减不增；逐步提取公共客户端和功能模块 |
| `api/static/*-center.js` | 对应运营中心的读取、渲染和交互 | 通过统一 API 客户端访问服务端 |
| `api/static/image-downloads.*` | 公开镜像地址呈现、响应式样式与复制交互 | 不读取认证状态或业务流程数据 |
| `api/static/agent-access.*` | 浏览器内审批、查看和吊销开发代理连接 | 只呈现非敏感元数据，不显示或复制 Bearer 凭据 |
| `api/static/project-activation.*` | 呈现项目激活步骤并编辑草稿契约质量命令 | 复用服务端激活报告，不在浏览器推导门禁 |
| `api/static/local-node-management.js` | 本机节点卡片、诊断、生命周期与镜像升级交互 | 只呈现服务端镜像差异和升级结果，不自行判断任务占用 |
| `api/static/styles.css` | 原生 token、布局和组件样式 | 历史大文件，只减不增；按稳定组件边界拆分 |

## 公共能力准入

只有跨多个稳定业务模块复用、没有业务所有者、并且接口稳定的能力才能进入公共层。金额、
权限、流程、治理、资源资格和发布规则必须留在其所有者模块，不得以 `utils` 或前端 helper
的名义复制。

依赖方向为 API → 服务 → 领域/持久化。规则检查不读取 Codex 主机文件，也不直接写入受管项目。
