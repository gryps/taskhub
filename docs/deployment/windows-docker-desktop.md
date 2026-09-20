# Windows Docker Desktop 部署 TaskHub

## 前置条件

- Windows 11 或受支持的 Windows 10；
- Docker Desktop，启用 WSL 2 后端与 Linux containers；
- Docker Compose v2；
- 建议给 Docker Desktop 分配至少 4 核 CPU、8 GB 内存和 40 GB 磁盘。

在普通 PowerShell 中运行 `docker info` 和 `docker compose version`。两条命令成功即可，不需要把 Windows 管理员密码、sudo 密码或 Docker Desktop 凭据提供给 TaskHub。

## 在线安装

TaskHub 镜像已公开，可任选一组地址：

| 来源 | Seed | Node |
| --- | --- | --- |
| GitHub GHCR | `ghcr.io/gryps/taskhub-seed:0.1.0-alpha` | `ghcr.io/gryps/taskhub-node:0.1.0-alpha` |
| 阿里云杭州 ACR | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-seed:0.1.0-alpha` | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-node:0.1.0-alpha` |

两个来源均支持匿名拉取；国内网络优先尝试阿里云 ACR。正式部署始终使用明确版本，不依赖 `latest`。

将标准交付目录放到固定位置，例如 `C:\taskhub`，在 PowerShell 执行：

```powershell
Set-Location C:\taskhub
.\init.ps1
```

如果 PowerShell 阻止本地脚本，可只对当前进程启用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\init.ps1
```

初始化脚本生成主机本地 `.env`，预拉取 Seed、Node、PostgreSQL 和 Socket Proxy 镜像，启动 Seed 控制面，创建初始自签名 TLS 证书并等待 HTTPS 健康检查。工作节点随后由 Web 在 Seed 本机 Docker 中创建。首次访问 `https://主机IP:8200`，确认初始证书指纹后，从 `.env` 读取一次性的 `TASKHUB_ADMIN_TOKEN` 设置管理员密码。对外开放前应把 `tls\taskhub.crt` 和 `tls\taskhub.key` 替换为企业 CA 或公开 CA 证书。

只有内部 Socket Proxy 挂载 Docker Socket；控制器不直接持有宿主机 Socket，Proxy 也不发布到 Windows 主机端口。

执行节点由 Seed 以 `seccomp=unconfined` 创建，使非 root 的 TaskHub 用户能够建立 Codex `workspace-write` 所需的用户命名空间。该放行只应用于执行节点，测试和预生产节点继续使用 Docker 默认 seccomp。执行节点挂载 `taskhub-data` 中隔离生成的 `config/model-accounts/node-runtime` 子目录，以允许 Codex 更新自身账号会话；其中仅包含当前编码角色需要的模型卡片和账号凭据，不暴露 Seed 的数据库、管理员、Git 或会话密钥。正式部署使用 `TASKHUB_WORKER_MODE=git`，否则流程只会返回本地演示结果而不会修改项目代码。

## 离线安装

在能联网的 Docker Desktop 构建机执行：

```powershell
$env:TASKHUB_VERSION = "0.1.0-alpha"
$env:TASKHUB_PLATFORM = "linux/amd64"
.\deploy\release\build-offline.ps1
```

把生成的整个 `dist\taskhub-offline-<version>-amd64` 复制到目标机，然后在该目录执行 `.\init.ps1`。脚本会验证 `SHA256SUMS` 后导入镜像；离线包包含 Node 镜像，供 Seed 本机创建工作节点。不要在 Intel/AMD 主机上导入 ARM64 包，反之亦然。

## 升级

在线升级：

```powershell
$env:TASKHUB_REGISTRY = "registry.example.com/team"
.\upgrade.ps1 -Version "0.2.0"
```

离线升级：

```powershell
.\upgrade.ps1 -Version "0.2.0" -OfflineBundle "D:\releases\taskhub-offline-0.2.0-amd64"
```

升级前会在 `backups\<UTC时间>` 生成数据库、TaskHub 数据卷、配置和旧镜像恢复点。备份期间控制器短暂停止；升级健康检查失败时自动恢复。

## 手工恢复

恢复会覆盖当前 `taskhub-data` 和 `taskhub-postgres-data` 两个 Docker 卷：

```powershell
.\restore.ps1 -BackupDirectory ".\backups\20260911T080000Z"
docker compose --env-file .env -f compose.yaml ps
[System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
Invoke-RestMethod https://127.0.0.1:8200/api/health
```

`.env`、`backups\` 和模型账户 `auth.json` 属于部署秘密，不得提交 Git、放入镜像或随离线安装包分发。
