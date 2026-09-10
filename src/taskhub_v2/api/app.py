import asyncio
from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from taskhub_v2.api.auth_routes import router as auth_router
from taskhub_v2.api.configuration_routes import router as configuration_router
from taskhub_v2.api.container_routes import router as container_router
from taskhub_v2.api.deployment_routes import router as deployment_router
from taskhub_v2.api.host_routes import router as host_router
from taskhub_v2.api.node_routes import router as node_router
from taskhub_v2.api.onboarding_routes import router as onboarding_router
from taskhub_v2.api.project_routes import router as project_router
from taskhub_v2.api.provider_routes import router as provider_router
from taskhub_v2.api.remote_node_routes import router as remote_node_router
from taskhub_v2.api.routes import router
from taskhub_v2.api.system_routes import router as system_router
from taskhub_v2.config import Settings, get_settings
from taskhub_v2.deployment import DeploymentManager
from taskhub_v2.persistence.checkpoints import checkpoint_store
from taskhub_v2.persistence.configuration import configuration_store
from taskhub_v2.persistence.hosts import physical_host_store
from taskhub_v2.persistence.remote_nodes import remote_node_store
from taskhub_v2.persistence.task_index import task_index_store
from taskhub_v2.projects import ProjectProvisioner, ProjectRegistry
from taskhub_v2.providers import build_provider
from taskhub_v2.providers.health import ProviderHealthStore
from taskhub_v2.security.auth import CSRF_COOKIE, SESSION_COOKIE, AuthService
from taskhub_v2.security.encryption import SecretCipher
from taskhub_v2.services import RunService
from taskhub_v2.services.configuration import ManagedConfigurationService
from taskhub_v2.services.containers import ContainerManager
from taskhub_v2.services.hosts import PhysicalHostService
from taskhub_v2.services.providers import ProviderCatalog
from taskhub_v2.services.remote_nodes import RemoteNodeService
from taskhub_v2.workers import (
    build_acceptance,
    build_coder,
    build_publisher,
    build_test_scheduler,
    build_worker,
)
from taskhub_v2.workers.coding_router import ScheduledCodingRouter
from taskhub_v2.workflows import build_main_graph


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    auth = AuthService(
        settings.admin_token,
        settings.session_secret,
        settings.admin_password_file,
    )
    projects = ProjectRegistry(settings.projects_file)
    project_provisioner = ProjectProvisioner(
        projects,
        settings.authority_git_host,
        settings.authority_git_root,
        settings.managed_repository_root,
    )
    provider_health = ProviderHealthStore(
        settings.provider_health_file,
        settings.provider_quota_cooldown_seconds,
        settings.provider_transient_cooldown_seconds,
    )
    default_container_manager = ContainerManager(
        enabled=settings.container_provisioning_enabled,
        socket_path=settings.docker_socket,
        network=settings.docker_network,
        image=settings.node_container_image,
        node_token=settings.node_token,
        nodes_file=settings.nodes_file,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with (
            configuration_store(settings) as managed_store,
            physical_host_store(settings) as host_store,
            remote_node_store(settings) as remote_store,
            checkpoint_store(settings) as checkpointer,
            task_index_store(settings) as task_index,
        ):
            cipher = (
                SecretCipher(settings.config_encryption_key)
                if settings.config_encryption_key
                else None
            )
            managed_configuration = ManagedConfigurationService(settings, managed_store, cipher)
            effective_settings = await managed_configuration.apply()
            app.state.settings = effective_settings
            app.state.managed_configuration = managed_configuration
            app.state.physical_hosts = PhysicalHostService(
                host_store,
                managed_store,
                cipher,
                effective_settings.node_callback_url,
            )
            if app.state.container_manager is default_container_manager:
                app.state.container_manager = ContainerManager(
                    enabled=effective_settings.container_provisioning_enabled,
                    socket_path=effective_settings.docker_socket,
                    network=effective_settings.docker_network,
                    image=effective_settings.node_container_image,
                    node_token=effective_settings.node_token,
                    nodes_file=effective_settings.nodes_file,
                )
            app.state.remote_nodes = RemoteNodeService(
                remote_store,
                app.state.physical_hosts,
                host_store,
                managed_store,
                app.state.container_manager,
                image=effective_settings.node_container_image,
                node_token=effective_settings.node_token,
                image_registry=effective_settings.node_image_registry,
                image_proxy=effective_settings.node_image_proxy,
                registry_username=effective_settings.node_registry_username,
                registry_password=effective_settings.node_registry_password,
                default_cpu=effective_settings.default_node_cpu_limit,
                default_memory=effective_settings.default_node_memory_limit,
            )
            provider = build_provider(effective_settings, provider_health)
            local_coder = build_coder(effective_settings, provider_health)
            test_scheduler = build_test_scheduler(effective_settings, local_coder)
            graph = build_main_graph(
                provider,
                build_worker(
                    effective_settings,
                    provider_health,
                    test_scheduler,
                    ScheduledCodingRouter(test_scheduler),
                ),
                checkpointer,
                build_publisher(effective_settings, test_scheduler),
                build_acceptance(effective_settings, test_scheduler),
            )
            app.state.run_service = RunService(
                graph,
                projects if effective_settings.worker_mode == "git" else None,
                task_index,
            )
            # A memory checkpointer is new for every process and has nothing to repair.
            # PostgreSQL can contain runs created before the task index existed.
            if effective_settings.checkpointer == "postgres":
                await app.state.run_service.backfill(checkpointer)
                app.state.recovery_task = asyncio.create_task(
                    app.state.run_service.recover_interrupted()
                )
            app.state.provider_catalog = ProviderCatalog(effective_settings, provider_health)
            app.state.node_scheduler = test_scheduler
            await managed_configuration.mark_applied()
            try:
                yield
            finally:
                await app.state.remote_nodes.close()
                recovery = getattr(app.state, "recovery_task", None)
                if recovery and not recovery.done():
                    recovery.cancel()
                close = getattr(provider, "close", None)
                if close:
                    await close()

    app = FastAPI(title="TaskHub V2", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.auth = auth
    app.state.projects = projects
    app.state.project_provisioner = project_provisioner
    app.state.deployment_manager = DeploymentManager(settings)
    app.state.container_manager = default_container_manager
    app.include_router(router)
    app.include_router(provider_router)
    app.include_router(auth_router)
    app.include_router(configuration_router)
    app.include_router(container_router)
    app.include_router(host_router)
    app.include_router(remote_node_router)
    app.include_router(project_router)
    app.include_router(node_router)
    app.include_router(onboarding_router)
    app.include_router(deployment_router)
    app.include_router(system_router)

    @app.middleware("http")
    async def require_authentication(request, call_next):
        path = request.url.path
        public = (
            path == "/"
            or path.startswith("/static/")
            or path
            in {
                "/api/health",
                "/api/auth/status",
                "/api/auth/login",
                "/api/auth/setup",
            }
        )
        if public:
            return await call_next(request)
        session = auth.read_session(request.cookies.get(SESSION_COOKIE))
        if not session:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not auth.valid_csrf(
            session,
            request.headers.get("x-csrf-token", ""),
            request.cookies.get(CSRF_COOKIE, ""),
        ):
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        return await call_next(request)

    static_dir = files("taskhub_v2.api").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(str(static_dir.joinpath("index.html")))

    return app
