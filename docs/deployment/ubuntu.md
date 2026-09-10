# Ubuntu 部署 TaskHub

## 前置条件

- Ubuntu 22.04/24.04 或兼容的 64 位 Linux；
- Docker Engine 24+，Docker Compose v2；
- 建议至少 4 核 CPU、8 GB 内存、40 GB 可用磁盘；
- 当前登录用户执行 `docker info` 时不需要 sudo。

TaskHub 安装脚本不会询问 sudo 密码。如果 `docker info` 报权限错误，应由管理员配置 Docker 用户组或 rootless Docker，重新登录后再安装。不要把 sudo 密码填入 TaskHub。

## 在线安装

TaskHub 镜像已公开，可任选一组地址：

| 来源 | Seed | Node |
| --- | --- | --- |
| GitHub GHCR | `ghcr.io/gryps/taskhub-seed:0.1.0-alpha` | `ghcr.io/gryps/taskhub-node:0.1.0-alpha` |
| 阿里云杭州 ACR | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-seed:0.1.0-alpha` | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-node:0.1.0-alpha` |

两个来源均支持匿名拉取；国内网络优先尝试阿里云 ACR。正式部署始终使用明确版本，不依赖 `latest`。

将标准交付目录放到固定位置，例如 `/opt/taskhub`，先按发布方提供的仓库地址修改 `.env.example` 中的三个镜像引用，或让初始化脚本生成默认配置后再编辑 `.env`。

```bash
cd /opt/taskhub
chmod +x ./*.sh
./init.sh
```

脚本会生成权限受限的 `.env`、拉取缺少的镜像、启动 Compose，并等待健康检查。首次访问 `http://主机IP:8200`，从 `.env` 读取一次性的 `TASKHUB_ADMIN_TOKEN` 设置管理员密码。

## 离线安装

在有网络、CPU 架构相同的构建机执行：

```bash
TASKHUB_VERSION=0.1.0-alpha TASKHUB_PLATFORM=linux/amd64 \
  ./deploy/release/build-offline.sh
```

把生成的整个 `dist/taskhub-offline-<version>-<arch>` 目录复制到目标机，不能只复制镜像 tar。目标机执行：

```bash
cd taskhub-offline-0.1.0-alpha-amd64
chmod +x ./*.sh
./init.sh
```

初始化先用 `SHA256SUMS` 校验镜像包、清单和 Compose，再执行 `docker load`。ARM64 主机必须使用 `TASKHUB_PLATFORM=linux/arm64` 单独生成的包。

## 升级

在线升级到已经发布到镜像仓库的新版本：

```bash
TASKHUB_REGISTRY=registry.example.com/team ./upgrade.sh 0.2.0
```

使用离线包升级：

```bash
./upgrade.sh 0.2.0 /mnt/releases/taskhub-offline-0.2.0-amd64
```

升级脚本先在 `backups/<UTC时间>` 创建恢复点，包含 PostgreSQL dump、TaskHub 数据卷、旧镜像、旧 `.env` 和旧 Compose。控制器在一致性备份期间会短暂停止。新版本三分钟内未通过健康检查时自动恢复。

## 手工恢复

恢复会覆盖当前 `taskhub-data` 和 `taskhub-postgres-data` 两个卷。确认恢复点后执行：

```bash
./restore.sh ./backups/20260911T080000Z
```

恢复后检查：

```bash
docker compose --env-file .env -f compose.yaml ps
curl -fsS http://127.0.0.1:8200/api/health
```

`.env`、`backups/` 和任何 `auth.json` 都含部署秘密，不得提交 Git 或发送给其他用户。
