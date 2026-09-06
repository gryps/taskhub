from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from taskhub_v2.api.auth_routes import router as auth_router
from taskhub_v2.api.node_routes import router as node_router
from taskhub_v2.api.project_routes import router as project_router
from taskhub_v2.api.provider_routes import router as provider_router
from taskhub_v2.api.routes import router
from taskhub_v2.config import Settings, get_settings
from taskhub_v2.persistence.checkpoints import checkpoint_store
from taskhub_v2.projects import ProjectProvisioner, ProjectRegistry
from taskhub_v2.providers import build_provider
from taskhub_v2.providers.health import ProviderHealthStore
from taskhub_v2.security.auth import CSRF_COOKIE, SESSION_COOKIE, AuthService
from taskhub_v2.services import RunService
from taskhub_v2.services.providers import ProviderCatalog
from taskhub_v2.workers import build_coder, build_publisher, build_test_scheduler, build_worker
from taskhub_v2.workers.coding_router import ScheduledCodingRouter
from taskhub_v2.workflows import build_main_graph


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    auth = AuthService(settings.admin_token, settings.session_secret)
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with checkpoint_store(settings) as checkpointer:
            provider = build_provider(settings, provider_health)
            local_coder = build_coder(settings, provider_health)
            test_scheduler = build_test_scheduler(settings, local_coder)
            graph = build_main_graph(
                provider,
                build_worker(
                    settings,
                    provider_health,
                    test_scheduler,
                    ScheduledCodingRouter(test_scheduler),
                ),
                checkpointer,
                build_publisher(settings, test_scheduler),
            )
            app.state.run_service = RunService(
                graph, projects if settings.worker_mode == "git" else None
            )
            app.state.provider_catalog = ProviderCatalog(settings, provider_health)
            app.state.node_scheduler = test_scheduler
            try:
                yield
            finally:
                close = getattr(provider, "close", None)
                if close:
                    await close()

    app = FastAPI(title="TaskHub V2", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.auth = auth
    app.state.projects = projects
    app.state.project_provisioner = project_provisioner
    app.include_router(router)
    app.include_router(provider_router)
    app.include_router(auth_router)
    app.include_router(project_router)
    app.include_router(node_router)

    @app.middleware("http")
    async def require_authentication(request, call_next):
        path = request.url.path
        public = path == "/" or path.startswith("/static/") or path in {
            "/api/health",
            "/api/auth/status",
            "/api/auth/login",
        }
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
