"""The Alembic first migration, verified against sqlite (no Postgres needed).

Portable types mean the same migration that manages Postgres in production runs
here, so upgrade/downgrade are exercised in CI. Skips if alembic isn't installed
(it's in the dev/migrations extra).
"""
import pathlib
import sqlite3

import pytest

pytest.importorskip("alembic")
from alembic import command          # noqa: E402
from alembic.config import Config    # noqa: E402

_INI = pathlib.Path("assetcore/db/alembic.ini").resolve()
_TABLES = {"asset", "facet_identity", "facet_source_version",
           "facet_runtime_version", "relationship", "event"}


def test_migration_upgrade_then_downgrade(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("ASSETCORE_DSN", f"sqlite:///{db}")
    cfg = Config(str(_INI))

    command.upgrade(cfg, "head")
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert _TABLES <= tables, f"missing tables after upgrade: {_TABLES - tables}"

    command.downgrade(cfg, "base")
    con = sqlite3.connect(db)
    remaining = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert not (_TABLES & remaining), f"tables survived downgrade: {_TABLES & remaining}"
