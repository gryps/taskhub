import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8200
    checkpointer: Literal["memory", "postgres"] = "memory"
    postgres_dsn: str = "postgresql://taskhub:taskhub@localhost:5432/taskhub"
    provider: Literal["deterministic", "openai", "routed"] = "deterministic"
    provider_secrets_file: str = "/home/gryps/.config/taskhub-v2/providers.env"
    provider_workdir: str = "/home/gryps/apps/taskhub-v2"
    admin_token: str = Field(default="", repr=False)
    session_secret: str = Field(default="", repr=False)
    cookie_secure: bool = False
    projects_file: str = "/home/gryps/.config/taskhub-v2/projects.json"
    authority_git_host: str = "gryps@192.168.31.3"
    authority_git_root: str = "/home/gryps/git"
    managed_repository_root: str = "/home/gryps/repos/taskhub-projects"
    self_deploy_enabled: bool = False
    self_deploy_project_id: str = ""
    self_deploy_target: str = ""
    self_deploy_service: str = "taskhub-v2.service"
    self_deploy_state_file: str = "/home/gryps/.local/state/taskhub-v2/deployment.json"
    self_deploy_health_url: str = "http://127.0.0.1:8200/api/health"
    workspace_root: str = "/home/gryps/workspaces/taskhub-v2"
    artifact_root: str = "/home/gryps/artifacts/taskhub-v2"
    preview_host: str = "127.0.0.1"
    provider_health_file: str = "/home/gryps/.local/state/taskhub-v2/provider-health.json"
    provider_quota_cooldown_seconds: int = 3600
    provider_transient_cooldown_seconds: int = 60
    worker_mode: Literal["local", "git"] = "local"
    test_runner: Literal["local", "scheduled"] = "local"
    nodes_file: str = "/home/gryps/.config/taskhub-v2/nodes.json"
    node_state_file: str = "/home/gryps/.local/state/taskhub-v2/node-state.json"
    node_token: str = Field(default="", repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_proxy_url: str = "http://192.168.31.200:7893"
    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = ""
    codex_cli_bin: str = "/home/gryps/.local/bin/codex"
    codex_plus_home: str = "/home/gryps/.codex-plus"
    codex_pro_home: str = "/home/gryps/.codex-pro"
    codex_api_home: str = "/home/gryps/.codex-api"
    gpt_api_key: str = Field(default="", repr=False)
    gpt_base_url: str = "https://api.openai.com/v1"
    gpt_model: str = "gpt-5.6-sol"
    gpt_planner_model: str = "gpt-5.6-terra"
    gpt_coder_model: str = "gpt-5.6-sol"
    gpt_supervisor_model: str = "gpt-5.6-sol"
    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-pro"
    minimax_api_key: str = Field(default="", repr=False)
    minimax_base_url: str = "https://api.minimax.cn/v1"
    minimax_model: str = "MiniMax-M3"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        env=os.getenv("TASKHUB_ENV", "development"),
        host=os.getenv("TASKHUB_HOST", "0.0.0.0"),
        port=int(os.getenv("TASKHUB_PORT", "8200")),
        checkpointer=os.getenv("TASKHUB_CHECKPOINTER", "memory"),
        postgres_dsn=os.getenv(
            "TASKHUB_POSTGRES_DSN", "postgresql://taskhub:taskhub@localhost:5432/taskhub"
        ),
        provider=os.getenv("TASKHUB_PROVIDER", "deterministic"),
        provider_secrets_file=os.getenv(
            "TASKHUB_PROVIDER_SECRETS_FILE", "/home/gryps/.config/taskhub-v2/providers.env"
        ),
        provider_workdir=os.getenv("TASKHUB_PROVIDER_WORKDIR", "/home/gryps/apps/taskhub-v2"),
        admin_token=os.getenv("TASKHUB_ADMIN_TOKEN", ""),
        session_secret=os.getenv("TASKHUB_SESSION_SECRET", ""),
        cookie_secure=os.getenv("TASKHUB_COOKIE_SECURE", "false").lower()
        in {"1", "true", "yes", "on"},
        projects_file=os.getenv(
            "TASKHUB_PROJECTS_FILE", "/home/gryps/.config/taskhub-v2/projects.json"
        ),
        authority_git_host=os.getenv("TASKHUB_AUTHORITY_GIT_HOST", "gryps@192.168.31.3"),
        authority_git_root=os.getenv("TASKHUB_AUTHORITY_GIT_ROOT", "/home/gryps/git"),
        managed_repository_root=os.getenv(
            "TASKHUB_MANAGED_REPOSITORY_ROOT",
            "/home/gryps/repos/taskhub-projects",
        ),
        self_deploy_enabled=os.getenv("TASKHUB_SELF_DEPLOY_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        self_deploy_project_id=os.getenv("TASKHUB_SELF_DEPLOY_PROJECT_ID", ""),
        self_deploy_target=os.getenv("TASKHUB_SELF_DEPLOY_TARGET", ""),
        self_deploy_service=os.getenv("TASKHUB_SELF_DEPLOY_SERVICE", "taskhub-v2.service"),
        self_deploy_state_file=os.getenv(
            "TASKHUB_SELF_DEPLOY_STATE_FILE",
            "/home/gryps/.local/state/taskhub-v2/deployment.json",
        ),
        self_deploy_health_url=os.getenv(
            "TASKHUB_SELF_DEPLOY_HEALTH_URL",
            "http://127.0.0.1:8200/api/health",
        ),
        workspace_root=os.getenv("TASKHUB_WORKSPACE_ROOT", "/home/gryps/workspaces/taskhub-v2"),
        artifact_root=os.getenv("TASKHUB_ARTIFACT_ROOT", "/home/gryps/artifacts/taskhub-v2"),
        preview_host=os.getenv("TASKHUB_PREVIEW_HOST", "127.0.0.1"),
        provider_health_file=os.getenv(
            "TASKHUB_PROVIDER_HEALTH_FILE",
            "/home/gryps/.local/state/taskhub-v2/provider-health.json",
        ),
        provider_quota_cooldown_seconds=int(
            os.getenv("TASKHUB_PROVIDER_QUOTA_COOLDOWN_SECONDS", "3600")
        ),
        provider_transient_cooldown_seconds=int(
            os.getenv("TASKHUB_PROVIDER_TRANSIENT_COOLDOWN_SECONDS", "60")
        ),
        worker_mode=os.getenv("TASKHUB_WORKER_MODE", "local"),
        test_runner=os.getenv("TASKHUB_TEST_RUNNER", "local"),
        nodes_file=os.getenv("TASKHUB_NODES_FILE", "/home/gryps/.config/taskhub-v2/nodes.json"),
        node_state_file=os.getenv(
            "TASKHUB_NODE_STATE_FILE",
            "/home/gryps/.local/state/taskhub-v2/node-state.json",
        ),
        node_token=os.getenv("TASKHUB_NODE_TOKEN", ""),
        openai_base_url=os.getenv("TASKHUB_OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_proxy_url=os.getenv("TASKHUB_OPENAI_PROXY_URL", "http://192.168.31.200:7893"),
        openai_api_key=os.getenv("TASKHUB_OPENAI_API_KEY", ""),
        openai_model=os.getenv("TASKHUB_OPENAI_MODEL", ""),
        codex_cli_bin=os.getenv("TASKHUB_CODEX_CLI_BIN", "/home/gryps/.local/bin/codex"),
        codex_plus_home=os.getenv("TASKHUB_CODEX_PLUS_HOME", "/home/gryps/.codex-plus"),
        codex_pro_home=os.getenv("TASKHUB_CODEX_PRO_HOME", "/home/gryps/.codex-pro"),
        codex_api_home=os.getenv("TASKHUB_CODEX_API_HOME", "/home/gryps/.codex-api"),
        gpt_api_key=os.getenv("TASKHUB_GPT_API_KEY", ""),
        gpt_base_url=os.getenv("TASKHUB_GPT_BASE_URL", "https://api.openai.com/v1"),
        gpt_model=os.getenv("TASKHUB_GPT_MODEL", "gpt-5.6-sol"),
        gpt_planner_model=os.getenv("TASKHUB_GPT_PLANNER_MODEL", "gpt-5.6-terra"),
        gpt_coder_model=os.getenv("TASKHUB_GPT_CODER_MODEL", "gpt-5.6-sol"),
        gpt_supervisor_model=os.getenv("TASKHUB_GPT_SUPERVISOR_MODEL", "gpt-5.6-sol"),
        deepseek_api_key=os.getenv("TASKHUB_DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.getenv("TASKHUB_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        deepseek_model=os.getenv("TASKHUB_DEEPSEEK_MODEL", "deepseek-v4-pro"),
        minimax_api_key=os.getenv("TASKHUB_MINIMAX_API_KEY", ""),
        minimax_base_url=os.getenv("TASKHUB_MINIMAX_BASE_URL", "https://api.minimax.cn/v1"),
        minimax_model=os.getenv("TASKHUB_MINIMAX_MODEL", "MiniMax-M3"),
    )
