"""infra/_providers.py — register the storage repos as "repo" providers.

This retires the last place a backing service is chosen by a conditional: the
hard-coded SqliteRepo default in service/app.py now builds through the same
registry that trackers use. Selecting sqlite vs postgres vs memory becomes a
config choice (assetcore.toml / ASSETCORE_CONFIG), not an `if/elif`.

Layering: infra may import the SDK's `providers` (a leaf registry outside the
inward stack; the layers contract is exhaustive=false, and the SDK never imports
infra back). psycopg2 is imported lazily inside the postgres factory so a box
without it gets a clean "provider unavailable" instead of an import crash here.

Import this module for its registration side-effects before building a "repo".
"""
from __future__ import annotations

from assetcore.infra.inmemory_repo import InMemoryRepo
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.sdk import providers


@providers.register("repo", "sqlite")
def _build_sqlite(config):
    # `or` (not `.get` default): an unset ${ASSETCORE_SQLITE_PATH} expands to "",
    # which is a key-present empty string — fall back to :memory: as documented,
    # rather than letting sqlite open an unintended anonymous on-disk temp db.
    return SqliteRepo(config.get("path") or ":memory:", check_same_thread=False)


@providers.register("repo", "memory")
def _build_memory(config):
    return InMemoryRepo()


@providers.register("repo", "postgres", requires=["dsn"])
def _build_postgres(config):
    # lazy so a missing psycopg2 surfaces only when postgres is actually selected
    from assetcore.infra.postgres_repo import PostgresRepo  # noqa: PLC0415
    kwargs = {}
    if config.get("min_conn"):
        kwargs["min_conn"] = int(config["min_conn"])
    if config.get("max_conn"):
        kwargs["max_conn"] = int(config["max_conn"])
    return PostgresRepo(config["dsn"], **kwargs)


# --- event sinks: the spine is a config choice too --------------------------
@providers.register("sink", "broadcast")
def _build_broadcast_sink(config):
    # in-process, bounded log — the single-process/dev default
    from assetcore.infra.broadcast_sink import BroadcastSink  # noqa: PLC0415
    kwargs = {}
    if config.get("max_log"):
        kwargs["max_log"] = int(config["max_log"])
    return BroadcastSink(**kwargs)


@providers.register("sink", "postgres", requires=["dsn"])
def _build_postgres_sink(config):
    # durable event table + LISTEN/NOTIFY fan-out — survives restarts, spans
    # processes. Lazy import: needs psycopg2 only when actually selected.
    from assetcore.infra.postgres_broadcast_sink import PostgresBroadcastSink  # noqa: PLC0415
    return PostgresBroadcastSink(config["dsn"])
