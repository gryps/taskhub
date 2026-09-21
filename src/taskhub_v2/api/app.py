import asyncio
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from taskhub_v2.api.auth_routes import router as auth_router
from taskhub_v2.api.canvas import mount_canvas
from taskhub_v2.api.capability_routes import router as capability_router
from taskhub_v2.api.configuration_routes import router as configuration_router
from taskhub_v2.api.container_routes import router as container_router
from taskhub_v2.api.dag_routes import router as dag_router
from taskhub_v2.api.deployment_routes import router as deployment_router
from taskhub_v2.api.diagnostic_routes import router as diagnostic_router
from taskhub_v2.api.node_routes import router as node_router
from taskhub_v2.api.onboarding_routes import router as onboarding_router
from taskhub_v2.api.productization_routes import router as productization_router
from taskhub_v2.api.project_contract_routes import router as project_contract_router
from taskhub_v2.api.project_routes import router as project_router
from taskhub_v2.api.provider_routes import router as provider_router
from taskhub_v2.api.revision_routes import router as revision_router
from taskhub_v2.api.routes import router
from taskhub_v2.api.system_routes import router as system_router
from taskhub_v2.api.topology_routes import router as topology_router
from taskhub_v2.config import Settings, get_settings
from taskhub_v2.deployment import DeploymentManager
from taskhub_v2.persistence.checkpoints import checkpoint_store
from taskhub_v2.persistence.configuration import configuration_store
from taskhub_v2.persistence.production import production_store
from taskhub_v2.persistence.task_index import task_index_store
from taskhub_v2.persistence.topologies import topology_store
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.providers import build_provider
from taskhub_v2.providers.health import ProviderHealthStore
from taskhub_v2.security.auth import CSRF_COOKIE, SESSION_COOKIE, AuthService
from taskhub_v2.security.backup_identity import backup_key_fingerprint
from taskhub_v2.security.encryption import SecretCipher
from taskhub_v2.security.http import required_permission as _required_permission
from taskhub_v2.security.http import secure_response as _secure_response
from taskhub_v2.security.node_credentials import NodeCredentialVault
from taskhub_v2.services import RunService
from taskhub_v2.services.capabilities import CapabilityService
from taskhub_v2.services.configuration import ManagedConfigurationService
from taskhub_v2.services.containers import ContainerManager
from taskhub_v2.services.dag_runtime import build_dag_runtime
from taskhub_v2.services.device_auth import CodexDeviceAuthService
from taskhub_v2.services.evidence import EvidenceCenterService
from taskhub_v2.services.exceptions import ExceptionCenterService
from taskhub_v2.services.git_authority import build_project_provisioner
from taskhub_v2.services.model_operations import ModelOperationsService
from taskhub_v2.services.node_model_config import write_node_model_configuration
from taskhub_v2.services.operational_log import OperationalLog
from taskhub_v2.services.productization import ProductizationService
from taskhub_v2.services.project_contracts import ProjectContractService
from taskhub_v2.services.project_preflight import ProjectPreflightService
from taskhub_v2.services.providers import ProviderCatalog
from taskhub_v2.services.revisions import RevisionService
from taskhub_v2.services.system_diagnostics import SystemDiagnosticsService
from taskhub_v2.services.topologies import TopologyService
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
        users_file=settings.users_file,
        session_state_file=settings.session_state_file,
        signing_keys_file=settings.session_signing_keys_file,
        session_idle_seconds=settings.session_idle_seconds,
        session_absolute_seconds=settings.session_absolute_seconds,
        login_max_failures=settings.login_max_failures,
        login_window_seconds=settings.login_window_seconds,
        login_lock_seconds=settings.login_lock_seconds,
    )
    projects = ProjectRegistry(settings.projects_file)
    default_project_provisioner = build_project_provisioner(settings, projects)
    provider_health = ProviderHealthStore(
        settings.provider_health_file,
        settings.provider_quota_cooldown_seconds,
        settings.provider_transient_cooldown_seconds,
        failure_threshold=settings.provider_failure_threshold,
        recovery_threshold=settings.provider_recovery_threshold,
        probe_interval_seconds=settings.provider_probe_interval_seconds,
        switch_lock_seconds=settings.provider_switch_lock_seconds,
    )
    operation_log = OperationalLog(settings.operations_log_file, settings.log_retention_days)
    default_container_manager = ContainerManager(
        enabled=settings.container_provisioning_enabled,
        socket_path=settings.docker_socket,
        network=settings.docker_network,
        image=settings.node_container_image,
        node_token=settings.node_token,
        nodes_file=settings.nodes_file,
        data_volume_name=settings.data_volume_name,
        model_accounts_volume_subpath=settings.model_accounts_volume_subpath,
        openai_proxy_url=settings.openai_proxy_url,
        operation_log=operation_log,
    )
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with (
            configuration_store(settings) as managed_store,
            production_store(settings) as production_objects,
            checkpoint_store(settings) as checkpointer,
            task_index_store(settings) as task_index,
            topology_store(settings) as topologies,
        ):
            await managed_store.ensure_backup_identity(
                backup_key_fingerprint(settings.config_encryption_key)
            )
            cipher = (
                SecretCipher(settings.config_encryption_key)
                if settings.config_encryption_key
                else None
            )
            managed_configuration = ManagedConfigurationService(settings, managed_store, cipher)
            effective_settings = await managed_configuration.apply()
            node_credentials = NodeCredentialVault(
                effective_settings.node_credentials_file,
                cipher,
            )
            persistent_codex = Path(effective_settings.model_account_root).parent / "bin/codex"
            if not Path(effective_settings.codex_cli_bin).is_file() and persistent_codex.is_file():
                effective_settings = effective_settings.model_copy(
                    update={"codex_cli_bin": str(persistent_codex)}
                )
            provider_health.failure_threshold = effective_settings.provider_failure_threshold
            provider_health.recovery_threshold = effective_settings.provider_recovery_threshold
            provider_health.probe_interval_seconds = (
                effective_settings.provider_probe_interval_seconds
            )
            provider_health.switch_lock_seconds = effective_settings.provider_switch_lock_seconds
            app.state.settings = effective_settings
            write_node_model_configuration(effective_settings)
            if app.state.project_provisioner is default_project_provisioner:
                app.state.project_provisioner = build_project_provisioner(
                    effective_settings, projects
                )
            app.state.production_objects = production_objects
            app.state.production_orchestration_enabled = (
                effective_settings.production_orchestration_enabled
            )
            app.state.managed_configuration = managed_configuration
            app.state.device_auth = CodexDeviceAuthService(
                effective_settings.codex_cli_bin,
                effective_settings.model_account_root,
                effective_settings.openai_proxy_url,
            )
            if app.state.container_manager is default_container_manager:
                app.state.container_manager = ContainerManager(
                    enabled=effective_settings.container_provisioning_enabled,
                    socket_path=effective_settings.docker_socket,
                    network=effective_settings.docker_network,
                    image=effective_settings.node_container_image,
                    node_token=effective_settings.node_token,
                    nodes_file=effective_settings.nodes_file,
                    data_volume_name=effective_settings.data_volume_name,
                    model_accounts_volume_subpath=(
                        effective_settings.model_accounts_volume_subpath
                    ),
                    openai_proxy_url=effective_settings.openai_proxy_url,
                    credentials=node_credentials,
                    operation_log=operation_log,
                )
            provider = build_provider(effective_settings, provider_health)
            app.state.capability_packs = CapabilityService(production_objects, projects)
            await app.state.capability_packs.ensure_builtins()
            app.state.productization = ProductizationService(
                production_objects, provider, app.state.capability_packs
            )
            local_coder = build_coder(effective_settings, provider_health)
            test_scheduler = build_test_scheduler(
                effective_settings,
                local_coder,
                token_resolver=node_credentials.resolve,
            )
            app.state.project_contracts = ProjectContractService(
                production_objects, projects, test_scheduler
            )
            app.state.project_preflight = ProjectPreflightService(
                effective_settings,
                projects,
                app.state.project_contracts,
                test_scheduler,
            )
            app.state.topologies = TopologyService(topologies, projects, test_scheduler)
            worker = build_worker(
                effective_settings,
                provider_health,
                test_scheduler,
                ScheduledCodingRouter(test_scheduler, app.state.topologies.eligible_node_ids),
            )
            app.state.dag_runtime = (
                build_dag_runtime(
                    production_objects,
                    projects,
                    worker,
                    test_scheduler,
                    app.state.topologies.eligible_node_ids,
                    app.state.capability_packs.design_for_refs,
                )
                if effective_settings.production_orchestration_enabled
                else None
            )
            app.state.revisions = (
                RevisionService(production_objects, projects, app.state.dag_runtime.planner)
                if app.state.dag_runtime
                else None
            )
            if app.state.dag_runtime:
                app.state.dag_runtime.revisions = app.state.revisions
            graph = build_main_graph(
                provider,
                worker,
                checkpointer,
                build_publisher(effective_settings, test_scheduler),
                build_acceptance(
                    effective_settings,
                    test_scheduler,
                    project_contracts=(
                        app.state.project_contracts
                        if effective_settings.production_orchestration_enabled
                        else None
                    ),
                ),
                production_runtime=app.state.dag_runtime,
            )
            app.state.run_service = RunService(
                graph,
                projects if effective_settings.worker_mode == "git" else None,
                task_index,
            )
            app.state.exception_center = ExceptionCenterService(
                task_index,
                projects if effective_settings.worker_mode == "git" else None,
            )
            app.state.evidence_center = EvidenceCenterService(app.state.run_service)
            # A memory checkpointer is new for every process and has nothing to repair.
            # PostgreSQL can contain runs created before the task index existed.
            if effective_settings.checkpointer == "postgres":
                await app.state.run_service.backfill(checkpointer)
                app.state.recovery_task = asyncio.create_task(
                    app.state.run_service.recover_interrupted()
                )
            app.state.provider_catalog = ProviderCatalog(effective_settings, provider_health)
            app.state.model_operations = ModelOperationsService(
                app.state.provider_catalog,
                app.state.run_service,
            )
            app.state.node_scheduler = test_scheduler
            app.state.node_credentials = node_credentials
            app.state.system_diagnostics = SystemDiagnosticsService(
                effective_settings,
                None,
                None,
                app.state.container_manager,
                test_scheduler,
                operation_log,
            )
            await managed_configuration.mark_applied()
            try:
                yield
            finally:
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
    app.state.project_provisioner = default_project_provisioner
    app.state.deployment_manager = DeploymentManager(settings)
    app.state.container_manager = default_container_manager
    app.state.operation_log = operation_log
    app.include_router(router)
    app.include_router(provider_router)
    app.include_router(auth_router)
    app.include_router(configuration_router)
    app.include_router(container_router)
    app.include_router(revision_router)
    app.include_router(capability_router)
    app.include_router(project_router)
    app.include_router(project_contract_router)
    app.include_router(dag_router)
    app.include_router(productization_router)
    app.include_router(node_router)
    app.include_router(onboarding_router)
    app.include_router(deployment_router)
    app.include_router(diagnostic_router)
    app.include_router(system_router)
    app.include_router(topology_router)
    mount_canvas(app)

    if settings.enforce_https:
        app.add_middleware(HTTPSRedirectMiddleware)
    allowed_hosts = [item.strip() for item in settings.trusted_hosts.split(",") if item.strip()]
    if allowed_hosts and allowed_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

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
            response = await call_next(request)
            return _secure_response(response)
        session = auth.read_session(request.cookies.get(SESSION_COOKIE))
        if not session:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        request.state.session = session
        permission = _required_permission(path, request.method)
        if permission and not auth.allowed(session, permission):
            operation_log.record(
                "access_denied",
                "forbidden",
                actor=session.get("actor"),
                role=session.get("role"),
                method=request.method,
                path=path,
            )
            return JSONResponse({"detail": "permission denied"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not auth.valid_csrf(
            session,
            request.headers.get("x-csrf-token", ""),
            request.cookies.get(CSRF_COOKIE, ""),
        ):
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        response = await call_next(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            operation_log.record(
                "management_api",
                "passed" if response.status_code < 400 else "failed",
                actor=session.get("actor"),
                role=session.get("role"),
                method=request.method,
                path=path,
                status_code=response.status_code,
            )
        return _secure_response(response)

    static_dir = files("taskhub_v2.api").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(str(static_dir.joinpath("index.html")))

    return app
