from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.browser import PreviewManager
from taskhub_v2.config import Settings
from taskhub_v2.execution import LocalTestScheduler, NodeScheduler
from taskhub_v2.execution.registry import NodeRegistry
from taskhub_v2.execution.runner import NodeRunner
from taskhub_v2.git import GitWorkspaceManager
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.providers.codex_account import CodexAccountProvider
from taskhub_v2.providers.health import ProviderHealthStore
from taskhub_v2.workers.acceptance import LocalAcceptanceGateway, ProjectAcceptanceGateway
from taskhub_v2.workers.base import PublisherGateway, WorkerGateway
from taskhub_v2.workers.coding_router import CodexCodingRouter
from taskhub_v2.workers.git_coder import GitCodingWorker
from taskhub_v2.workers.local import LocalWorker
from taskhub_v2.workers.publisher import GitPublisher, LocalPublisher


def build_worker(
    settings: Settings,
    health: ProviderHealthStore | None = None,
    test_scheduler=None,
    coder=None,
) -> WorkerGateway:
    if settings.worker_mode == "local":
        return LocalWorker()
    coder = coder or build_coder(settings, health)
    return GitCodingWorker(
        projects=ProjectRegistry(settings.projects_file),
        workspaces=GitWorkspaceManager(settings.workspace_root),
        coder=coder,
        artifacts=ArtifactStore(settings.artifact_root),
        test_scheduler=test_scheduler,
    )


def build_coder(settings: Settings, health: ProviderHealthStore | None = None):
    common = {
        "codex_bin": settings.codex_cli_bin,
        "proxy_url": settings.openai_proxy_url,
        "workdir": settings.provider_workdir,
        "timeout": 1200,
    }
    if settings.model_cards:
        assigned = sorted(
            (
                item["priority"], card
            )
            for card in settings.model_cards
            if card.get("enabled") and card["service_type"] == "openai"
            for item in card.get("assignments", [])
            if item["role"] == "coder"
        )
        providers = [
            CodexAccountProvider(
                card["model_id"],
                codex_home=f"{settings.model_account_root}/{card['model_id']}",
                model=card.get("model") or "account_default",
                api_key=card.get("api_key", ""),
                **common,
            )
            for _, card in assigned
        ]
    else:
        providers = [
            CodexAccountProvider(
                "chatgpt_plus_account", codex_home=settings.codex_plus_home, **common
            ),
            CodexAccountProvider(
                "chatgpt_pro_account", codex_home=settings.codex_pro_home, **common
            ),
            CodexAccountProvider(
                "gpt_api",
                codex_home=settings.codex_api_home,
                model=settings.gpt_coder_model,
                api_key=settings.gpt_api_key,
                **common,
            ),
        ]
    return CodexCodingRouter(providers, health=health)


def build_publisher(settings: Settings, test_scheduler=None) -> PublisherGateway:
    if settings.worker_mode == "local":
        return LocalPublisher()
    return GitPublisher(
        ProjectRegistry(settings.projects_file), settings.workspace_root, test_scheduler
    )


def build_acceptance(settings: Settings, test_scheduler=None):
    if settings.worker_mode == "local":
        return LocalAcceptanceGateway()
    return ProjectAcceptanceGateway(
        ProjectRegistry(settings.projects_file),
        test_scheduler or build_test_scheduler(settings),
        ArtifactStore(settings.artifact_root),
        PreviewManager(settings.postgres_dsn, host=settings.preview_host),
    )


def build_test_scheduler(settings: Settings, local_coder=None, token_resolver=None):
    if settings.test_runner == "local":
        return LocalTestScheduler()
    return NodeScheduler(
        NodeRegistry(settings.nodes_file),
        NodeRunner(
            settings.node_token,
            local_coder=local_coder,
            token_resolver=token_resolver,
        ),
        settings.node_state_file,
    )
