"""FastAPI app factory — wires a backend + sink + auth into the routes.

`create_app()` is the single composition root for the service. Defaults are the
runnable-here choices (SQLite + the in-process BroadcastSink); a different repo or
sink (e.g. PostgresRepo, a future notify_sink) drops in unchanged — the same port
swap that makes tools and storage disposable. `uvicorn assetcore.service.app:app`
runs the default instance.
"""
import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("assetcore.service")

from assetcore.app.services import AssetcoreService
from assetcore.core.ports import AssetRepo, EventSink
from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.service import auth
from assetcore.service.routes import router


def create_app(
    repo: AssetRepo | None = None,
    sink: EventSink | None = None,
    tokens: dict[str, str] | None = None,
) -> FastAPI:
    if repo is None or sink is None:
        # backend selection is config-driven (no if/elif): build through the same
        # provider registry the trackers use. ASSETCORE_CONFIG names a repo/sink in
        # the toml; absent that, the runnable-here defaults (sqlite :memory: + the
        # in-process BroadcastSink) — still via the registry for the repo, so there
        # is one mechanism for every service swap.
        import assetcore.infra._providers  # noqa: F401 — runs repo/sink registrations
        from assetcore.sdk import providers

        cfg_path = os.environ.get("ASSETCORE_CONFIG")
        settings = None
        if cfg_path:
            from assetcore.sdk.settings import Settings
            settings = Settings.load(cfg_path)
            settings.validate(["repo", "sink"])  # fail fast on a bad config at startup
        if repo is None:
            if settings is not None:
                repo = settings.repo("main")
            else:
                repo = providers.build("repo", "sqlite",
                                       {"path": os.environ.get("ASSETCORE_SQLITE_PATH", ":memory:")})
        if sink is None:
            if settings is not None and settings.has_section("sinks"):
                sink = settings.sink("main")
            else:
                sink = BroadcastSink()

    app = FastAPI(title="assetcore", version="0.1.0",
                  summary="Identity-first asset management — the only door (L2).")
    app.state.service = AssetcoreService(repo, sink)
    app.state.sink = sink
    app.state.tokens = tokens if tokens is not None else auth.load_tokens()
    app.state.latency = {"count": 0, "total_ms": 0.0, "max_ms": 0.0}

    @app.middleware("http")
    async def _time_requests(request: Request, call_next):
        # correlate a request end-to-end: reuse an inbound X-Request-ID or mint one,
        # echo it back, and log one structured line per request (logging config is
        # left to the host/uvicorn — a library shouldn't hijack the root logger).
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        lat = request.app.state.latency
        lat["count"] += 1
        lat["total_ms"] += elapsed_ms
        lat["max_ms"] = max(lat["max_ms"], elapsed_ms)
        response.headers["X-Request-ID"] = request_id
        logger.info("request method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
                    request.method, request.url.path, response.status_code, elapsed_ms, request_id)
        return response

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Handled errors (HTTPException/validation) already flow back through the
        # middleware with an X-Request-ID; this catches the UNHANDLED case so a 500
        # also carries the id and gets logged, instead of escaping above the
        # middleware with neither.
        request_id = getattr(request.state, "request_id", None)
        logger.exception("unhandled error method=%s path=%s request_id=%s",
                         request.method, request.url.path, request_id)
        return JSONResponse(status_code=500, content={"detail": "internal server error"},
                            headers={"X-Request-ID": request_id} if request_id else None)

    app.include_router(router)
    return app


# Default instance for `uvicorn assetcore.service.app:app`.
app = create_app()
