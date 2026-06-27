"""
assetcore.db.connection — database access layer.

Two backends behind one tiny interface (.execute / .fetchone / .fetchall):

  * SqliteDB  — zero-setup, in-memory or file. For local exploration & tests.
                Translates the Postgres-dialect schema.sql on the fly so the
                SAME schema file and the SAME api.py run unchanged.
  * PostgresDB — the production target (psycopg2/asyncpg). Stub wiring included;
                 flesh out when you move off SQLite.

The point of this layer: api.py never knows which backend it's talking to. It
calls .execute/.fetchone/.fetchall and traffics in '?' placeholders. Keeping the
api backend-agnostic is what lets you prototype on SQLite and deploy on Postgres
without touching the business logic.
"""
from __future__ import annotations
import os, re, sqlite3, pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


class SqliteDB:
    """In-memory (or file) SQLite that accepts the Postgres-flavored schema/api."""

    def __init__(self, path: str = ":memory:"):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self):
        ddl = SCHEMA_PATH.read_text()
        # --- translate just enough PG -> SQLite to validate the model locally ---
        ddl = re.sub(r"CREATE EXTENSION.*?;", "", ddl, flags=re.S)
        ddl = ddl.replace("UUID PRIMARY KEY DEFAULT gen_random_uuid()", "TEXT PRIMARY KEY")
        ddl = ddl.replace("UUID", "TEXT").replace("JSONB", "TEXT")
        ddl = ddl.replace("TIMESTAMPTZ NOT NULL DEFAULT now()", "TEXT DEFAULT CURRENT_TIMESTAMP")
        ddl = ddl.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        ddl = ddl.replace("TEXT[] NOT NULL DEFAULT '{}'", "TEXT DEFAULT '[]'")
        ddl = ddl.replace("'{}'::jsonb", "'{}'")
        ddl = re.sub(
            r"CREATE UNIQUE INDEX (\w+)\s+ON (\w+)\((\w+)\) WHERE is_latest;",
            r"CREATE UNIQUE INDEX \1 ON \2(\3) WHERE is_latest=1;", ddl)
        self.c.executescript(ddl)

    def execute(self, q, params=()):
        self.c.execute(q.replace("now()", "CURRENT_TIMESTAMP"), params)
        self.c.commit()

    def fetchone(self, q, params=()):
        r = self.c.execute(q, params).fetchone()
        return dict(r) if r else None

    def fetchall(self, q, params=()):
        return [dict(r) for r in self.c.execute(q, params).fetchall()]

    def close(self):
        self.c.close()


class PostgresDB:
    """Production backend. Requires psycopg2-binary and a running Postgres.

    NOTE: api.py uses '?' placeholders (SQLite style). psycopg2 uses '%s'. The
    cleanest production move is to standardize api.py on a named/paramstyle that
    both accept, or translate here. Left explicit so the seam is obvious rather
    than magic. See docs/ROADMAP.md item 'DB paramstyle unification'.
    """

    def __init__(self, dsn: str | None = None):
        import psycopg2, psycopg2.extras  # imported lazily so SQLite users need no pg
        self._psycopg2 = psycopg2
        dsn = dsn or os.environ.get("ASSETCORE_DSN", "postgresql://localhost/assetcore")
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True
        self._dict = psycopg2.extras.RealDictCursor

    @staticmethod
    def _q(q):  # naive '?' -> '%s'; fine for these simple statements
        return q.replace("?", "%s")

    def execute(self, q, params=()):
        with self.conn.cursor() as cur:
            cur.execute(self._q(q), params)

    def fetchone(self, q, params=()):
        with self.conn.cursor(cursor_factory=self._dict) as cur:
            cur.execute(self._q(q), params)
            r = cur.fetchone()
            return dict(r) if r else None

    def fetchall(self, q, params=()):
        with self.conn.cursor(cursor_factory=self._dict) as cur:
            cur.execute(self._q(q), params)
            return [dict(r) for r in cur.fetchall()]

    def close(self):
        self.conn.close()


def get_db(backend: str | None = None, **kw):
    """Factory. ASSETCORE_BACKEND=postgres selects Postgres; default is sqlite."""
    backend = backend or os.environ.get("ASSETCORE_BACKEND", "sqlite")
    if backend == "postgres":
        return PostgresDB(**kw)
    return SqliteDB(**kw)
