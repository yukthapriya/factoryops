import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from factoryops.api.routes import api
from factoryops.config import get_settings
from factoryops.errors import install_error_handlers
from factoryops.live import hub, serve_client
from factoryops.logging_setup import configure_logging, request_id_var

log = logging.getLogger("factoryops.http")


def create_app(start_listener: bool = True) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_listener:
            await hub.start()
        yield
        if start_listener:
            await hub.stop()

    app = FastAPI(
        title="FactoryOps API",
        version="0.1.0",
        description="Simulated manufacturing operations & quality intelligence. "
        "All data is synthetic.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if 0 < len(incoming) <= 64 and incoming.isprintable() else uuid.uuid4().hex
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        log.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    install_error_handlers(app)
    app.include_router(api)

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await serve_client(ws)

    return app


app = create_app()
