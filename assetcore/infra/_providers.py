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


@providers.register("repo", "postgres")
def _build_postgres(config):
    # lazy so a missing psycopg2 surfaces only when postgres is actually selected
    from assetcore.infra.postgres_repo import PostgresRepo  # noqa: PLC0415
    return PostgresRepo(config["dsn"])
