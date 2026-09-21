from __future__ import annotations

from taskhub_v2.config import Settings
from taskhub_v2.security.rbac import ROLE_PERMISSIONS


def production_readiness(settings: Settings, app_state) -> dict:
    checks = [
        _check(
            "persistent_orchestration",
            "持久化生产编排",
            settings.production_orchestration_enabled and settings.checkpointer == "postgres",
            "需要启用产品化编排并使用 PostgreSQL checkpoint",
        ),
        _check(
            "https_session",
            "HTTPS 与安全会话",
            settings.enforce_https and settings.cookie_secure,
            "正式环境必须同时强制 HTTPS 和 Secure Cookie",
        ),
        _check(
            "session_rotation",
            "持久会话与密钥轮换",
            bool(settings.session_state_file and settings.session_signing_keys_file),
            "需要持久化会话状态与签名密钥文件",
        ),
        _check(
            "rbac",
            "正式角色权限",
            {"administrator", "project_owner", "developer", "auditor"}.issubset(ROLE_PERMISSIONS),
            "需要管理员、项目负责人、开发人员和只读审计角色",
        ),
        _check(
            "audit",
            "管理操作审计",
            bool(settings.operations_log_file),
            "需要持久化脱敏操作审计路径",
        ),
        _check(
            "backup_identity",
            "备份恢复密钥身份",
            bool(settings.config_encryption_key),
            "需要配置加密主密钥并绑定数据库备份身份",
        ),
        _check(
            "docker_isolation",
            "Docker Socket 隔离",
            settings.docker_socket.startswith(("http://", "https://")),
            "正式控制器应连接受限 Socket Proxy，不直接挂载 Docker Socket",
        ),
        _check(
            "local_node_management",
            "本机节点管理",
            settings.container_provisioning_enabled
            and bool(getattr(app_state, "container_manager", None)),
            "需要启用 Seed 本机 Docker 节点管理",
        ),
    ]
    return {
        "ready": all(item["passed"] for item in checks),
        "checks": checks,
        "summary": {
            "passed": sum(item["passed"] for item in checks),
            "total": len(checks),
        },
    }


def _check(check_id: str, title: str, passed: bool, remediation: str) -> dict:
    return {
        "id": check_id,
        "title": title,
        "passed": passed,
        "remediation": "" if passed else remediation,
    }
