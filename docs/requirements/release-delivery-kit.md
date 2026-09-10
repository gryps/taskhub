# TaskHub 标准交付包需求

## 目标

TaskHub 的安装者只需准备一台能运行 Linux 容器的 Docker 主机。在线环境可从镜像仓库拉取，离线环境可导入同版本、同 CPU 架构的完整镜像包。宿主机不需要 Python、Node.js、Codex CLI 或 TaskHub 源码。

## 交付单元

| 单元 | 固定职责 |
| --- | --- |
| `taskhub-seed:<version>` | Web、API、LangGraph、主机与节点编排、模型设备授权 |
| `taskhub-node:<version>` | 执行/测试/预生产共用工作环境，由角色开关限制工作负载 |
| `postgres:16-alpine` | Seed 元数据与 LangGraph checkpoint 持久化 |
| `compose.yaml` | 启动 Seed 和 PostgreSQL；Node 镜像只预载，由 Seed 按需创建角色容器 |
| 初始化脚本 | 检查 Docker、生成部署密钥、导入或拉取镜像、启动并等待健康 |
| 离线包 | 三个镜像、版本清单、Compose、脚本、文档与 SHA-256 校验文件 |
| 升级/恢复脚本 | 升级前生成一致性恢复点；失败时回退数据、配置和镜像 |

## 约束

- 发布镜像不得包含 `.env`、API Key、模型令牌、SSH 私钥、管理员密码、项目仓库或运行数据。
- 正式 Compose 不得挂载 TaskHub 源码；源码挂载只属于快速开发环境。
- `.env` 首次生成后只保存在安装主机，权限应限制为当前管理员。
- Linux 初始化不调用 `sudo`。运行用户必须已经能直接执行 `docker info`；如果不能，由系统管理员预先配置 Docker 组或 rootless Docker。
- Windows 使用 Docker Desktop 的 Linux containers 模式与 Compose v2。
- 一个离线包只对应一个 Linux CPU 架构。`linux/amd64` 与 `linux/arm64` 必须分别构建。
- 升级前必须备份 PostgreSQL、自有数据卷、当前 `.env`、Compose 和旧镜像。恢复会覆盖当前 TaskHub 两个命名卷，必须显式指定恢复点。
- 离线包和备份均用 SHA-256 校验；清单记录镜像 ID、产品版本和平台。

## 完成标准

1. 在线和离线初始化均能启动健康的 Seed。
2. `taskhub-node` 已在 Seed Docker Engine 中可见，Web 创建节点无需宿主机再次构建。
3. Seed 镜像具备 Codex 设备授权命令；Node 镜像具备 Git、Python、pytest、Node.js/npm、Playwright Python 包和 Codex CLI。
4. 初始化后只有 Seed Web 端口对宿主机发布；PostgreSQL不发布端口。
5. 升级成功后保留管理员、模型、物理主机、节点和任务数据。
6. 升级健康检查失败时能使用刚生成的恢复点回到原镜像和原数据。
