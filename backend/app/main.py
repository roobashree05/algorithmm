import asyncio
import contextlib
import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.errors import error_body, register_exception_handlers
from app.api.routes import admin, bugs, health, summaries, webhooks
from app.core.config import Settings, get_settings
from app.core.logging import correlation_id_var, setup_logging
from app.core.rate_limit import build_rate_limiter
from app.db.session import build_engine, build_session_factory
from app.integrations.gitlab.factory import build_gitlab_provider
from app.integrations.whatsapp.factory import build_whatsapp_provider
from app.services.attachments import AttachmentStore
from app.workers.outbox_worker import process_outbox_batch

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    AttachmentStore(settings.attachment_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = None
        if app.state.settings.enable_outbox_worker:
            task = asyncio.create_task(_outbox_loop(app, stop))
        yield
        stop.set()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        app.state.engine.dispose()

    app = FastAPI(
        title="WhatsApp GitLab Bug Tracker",
        version="0.1.0",
        summary="Local bug reporting platform for WhatsApp and GitLab.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = build_engine(settings.database_url)
    app.state.session_factory = build_session_factory(app.state.engine)
    app.state.gitlab = build_gitlab_provider(settings)
    app.state.whatsapp = build_whatsapp_provider(settings)
    app.state.rate_limiter = build_rate_limiter(settings.redis_url, settings.rate_limit_per_minute)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_and_rate_limit(request: Request, call_next):
        incoming = request.headers.get("x-correlation-id") or ""
        cleaned = "".join(character for character in incoming if character.isalnum() or character in "-_")[:100]
        token = correlation_id_var.set(cleaned or _new_correlation_id())
        try:
            if request.url.path not in {"/health", "/ready"}:
                source = request.headers.get("authorization") or (request.client.host if request.client else "unknown")
                key = hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]
                if not request.app.state.rate_limiter.allow(key):
                    response = JSONResponse(
                        status_code=429,
                        content=error_body("rate_limited", "Too many requests"),
                        headers={"Retry-After": "60"},
                    )
                    response.headers["X-Correlation-ID"] = correlation_id_var.get()
                    return response
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id_var.get()
            logger.info("request", extra={"method": request.method, "path": request.url.path, "status_code": response.status_code})
            return response
        finally:
            correlation_id_var.reset(token)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(bugs.router)
    app.include_router(admin.router)
    app.include_router(summaries.router)
    app.include_router(webhooks.router)
    return app


def _new_correlation_id() -> str:
    import uuid

    return uuid.uuid4().hex


async def _outbox_loop(app: FastAPI, stop: asyncio.Event) -> None:
    settings = app.state.settings
    while not stop.is_set():
        try:
            await asyncio.to_thread(process_outbox_batch, app.state.session_factory, app.state.whatsapp, settings)
        except Exception:
            logger.exception("outbox batch failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.outbox_poll_seconds)
        except TimeoutError:
            continue


app = create_app()
