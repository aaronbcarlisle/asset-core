"""FastAPI app factory — wires a backend + sink + auth into the routes.

`create_app()` is the single composition root for the service. Defaults are the
runnable-here choices (SQLite + the in-process BroadcastSink); a different repo or
sink (e.g. PostgresRepo, a future notify_sink) drops in unchanged — the same port
swap that makes tools and storage disposable. `uvicorn assetcore.service.app:app`
runs the default instance.
"""
import os
import time

from fastapi import FastAPI, Request

from assetcore.app.services import AssetcoreService
from assetcore.core.ports import AssetRepo, EventSink
from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.service import auth
from assetcore.service.routes import router


def create_app(
    repo: AssetRepo | None = None,
    sink: EventSink | None = None,
    tokens: dict[str, str] | None = None,
) -> FastAPI:
    if repo is None:
        # one process-lifetime connection, reached from the event-loop worker thread
        repo = SqliteRepo(os.environ.get("ASSETCORE_SQLITE_PATH", ":memory:"),
                          check_same_thread=False)
    if sink is None:
        sink = BroadcastSink()

    app = FastAPI(title="assetcore", version="0.1.0",
                  summary="Identity-first asset management — the only door (L2).")
    app.state.service = AssetcoreService(repo, sink)
    app.state.sink = sink
    app.state.tokens = tokens if tokens is not None else auth.load_tokens()
    app.state.latency = {"count": 0, "total_ms": 0.0, "max_ms": 0.0}

    @app.middleware("http")
    async def _time_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        lat = request.app.state.latency
        lat["count"] += 1
        lat["total_ms"] += elapsed_ms
        lat["max_ms"] = max(lat["max_ms"], elapsed_ms)
        return response

    app.include_router(router)
    return app


# Default instance for `uvicorn assetcore.service.app:app`.
app = create_app()
