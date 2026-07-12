"""Schema parity: the DDL the repos bootstrap from (`infra/schema.sql`) and the
Alembic migration that manages production must describe the SAME tables/columns.

They are two expressions of the same five-plus-events model; this test fails if
they drift (e.g. a column added to one but not the other). Both are materialized
on sqlite (no Postgres needed) and compared by table -> column-name set.
"""
import pathlib
import sqlite3

import pytest

pytest.importorskip("alembic")
from alembic import command          # noqa: E402
from alembic.config import Config    # noqa: E402

from assetcore.infra.sqlite_repo import SqliteRepo   # noqa: E402

_INI = pathlib.Path("assetcore/db/alembic.ini").resolve()
_TABLES = ["asset", "facet_identity", "facet_source_version",
           "facet_runtime_version", "relationship", "event"]


def _columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out = {}
    for table in _TABLES:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        out[table] = {r[1] for r in rows}     # r[1] = column name
    return out


def test_repo_schema_matches_alembic_migration(tmp_path, monkeypatch):
    # 1) the schema the repos bootstrap from (infra/schema.sql, translated to sqlite)
    repo = SqliteRepo(":memory:")
    repo_cols = _columns(repo.conn)
    repo.close()

    # 2) the schema Alembic produces (the managed production path), on sqlite
    db = tmp_path / "m.db"
    monkeypatch.setenv("ASSETCORE_DSN", f"sqlite:///{db}")
    command.upgrade(Config(str(_INI)), "head")
    con = sqlite3.connect(db)
    migration_cols = _columns(con)
    con.close()

    assert repo_cols == migration_cols, (
        "infra/schema.sql and the Alembic migration have drifted:\n"
        + "\n".join(f"  {t}: repo-only={sorted(repo_cols[t] - migration_cols[t])} "
                    f"migration-only={sorted(migration_cols[t] - repo_cols[t])}"
                    for t in _TABLES if repo_cols[t] != migration_cols[t]))
