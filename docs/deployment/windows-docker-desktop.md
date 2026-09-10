# Windows Docker Desktop 部署 TaskHub

## 前置条件

- Windows 11 或受支持的 Windows 10；
- Docker Desktop，启用 WSL 2 后端与 Linux containers；
- Docker Compose v2；
- 建议给 Docker Desktop 分配至少 4 核 CPU、8 GB 内存和 40 GB 磁盘。

在普通 PowerShell 中运行 `docker info` 和 `docker compose version`。两条命令成功即可，不需要把 Windows 管理员密码、sudo 密码或 Docker Desktop 凭据提供给 TaskHub。

## 在线安装

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

初始化脚本生成主机本地 `.env`，拉取缺少的 Seed、Node 和 PostgreSQL 镜像，启动 Compose 并等待健康检查。首次访问 `http://主机IP:8200`，从 `.env` 读取一次性的 `TASKHUB_ADMIN_TOKEN` 设置管理员密码。

## 离线安装

在能联网的 Docker Desktop 构建机执行：

```powershell
$env:TASKHUB_VERSION = "0.1.0-alpha"
$env:TASKHUB_PLATFORM = "linux/amd64"
.\deploy\release\build-offline.ps1
```

把生成的整个 `dist\taskhub-offline-<version>-amd64` 复制到目标机，然后在该目录执行 `.\init.ps1`。脚本会验证 `SHA256SUMS` 后导入镜像。不要在 Intel/AMD 主机上导入 ARM64 包，反之亦然。

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
Invoke-RestMethod http://127.0.0.1:8200/api/health
```

`.env`、`backups\` 和模型账户 `auth.json` 属于部署秘密，不得提交 Git、放入镜像或随离线安装包分发。
