"""SqliteRepo — an AssetRepo backed by stdlib sqlite3 (zero external deps).

Satisfies core.ports.AssetRepo over the canonical infra/schema.sql, translated
from PostgreSQL dialect to SQLite on the fly (same trick as the prototype's
connection.py). All (de)serialization at the storage boundary lives here:
UUID<->str, dict/list<->json, enum<->value, bool<->0/1, datetime<->isoformat.

The one-latest invariant is enforced at write time inside add_*_version (demote
prior latest, then insert) — the resolution of PHASE1 decision #4, mirroring the
schema's one_latest_* partial unique indexes.
"""
import json
import pathlib
import re
import sqlite3
from datetime import datetime
from uuid import UUID

from assetcore.core.entities import (
    Asset,
    IdentityFacet,
    Relationship,
    RuntimeVersion,
    SourceVersion,
)
from assetcore.core.errors import VersionConflict
from assetcore.core.types import BindingMode, Lifecycle, RelType

_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def _translate_pg_to_sqlite(ddl: str) -> str:
    """Just enough PostgreSQL -> SQLite to run the canonical schema locally."""
    ddl = re.sub(r"CREATE EXTENSION.*?;", "", ddl, flags=re.S)
    ddl = ddl.replace("UUID PRIMARY KEY DEFAULT gen_random_uuid()", "TEXT PRIMARY KEY")
    ddl = ddl.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    ddl = ddl.replace("TEXT[] NOT NULL DEFAULT '{}'", "TEXT NOT NULL DEFAULT '[]'")
    ddl = ddl.replace("BOOLEAN NOT NULL DEFAULT TRUE", "INTEGER NOT NULL DEFAULT 1")
    ddl = ddl.replace("'{}'::jsonb", "'{}'")
    ddl = ddl.replace("UUID", "TEXT").replace("JSONB", "TEXT")
    ddl = ddl.replace("TIMESTAMPTZ", "TEXT").replace("now()", "CURRENT_TIMESTAMP")
    ddl = ddl.replace("BOOLEAN", "INTEGER")
    ddl = ddl.replace("WHERE is_latest;", "WHERE is_latest = 1;")
    return ddl


# --- boundary (de)serialization helpers ------------------------------------
def _dt(value: datetime) -> str:
    return value.isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteRepo:
    """Satisfies core.ports.AssetRepo."""

    def __init__(self, path: str = ":memory:", check_same_thread: bool = True) -> None:
        # The service accesses one connection from FastAPI's worker thread, so it
        # passes check_same_thread=False. Access there is single-threaded (all
        # routes run on one event loop), so this stays safe.
        self.conn = sqlite3.connect(path, check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_translate_pg_to_sqlite(_SCHEMA_PATH.read_text()))
        self._init_identity_fts()

    # --- similarity index (FTS5) --------------------------------------------
    # identity_fts mirrors each identity's human-facing text (display_name +
    # taxonomy + tags) so search_candidates never scans the whole catalog. It is
    # a local index, not part of the canonical schema (Postgres uses pg_trgm).
    def _init_identity_fts(self) -> None:
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS identity_fts"
            " USING fts5(asset_id UNINDEXED, text)")
        # backfill: a database created before this index existed has identities
        # but an empty fts table — rebuild once so search sees them.
        has_rows = self.conn.execute("SELECT 1 FROM identity_fts LIMIT 1").fetchone()
        if has_rows is None:
            rows = self.conn.execute(
                "SELECT asset_id, display_name, taxonomy, tags FROM facet_identity").fetchall()
            with self.conn:
                for r in rows:
                    text = self._identity_text(r["display_name"], r["taxonomy"],
                                               json.loads(r["tags"]))
                    if text:
                        self.conn.execute(
                            "INSERT INTO identity_fts (asset_id, text) VALUES (?, ?)",
                            (r["asset_id"], text))

    @staticmethod
    def _identity_text(display_name: str | None, taxonomy: str | None,
                       tags: list[str]) -> str:
        return " ".join(filter(None, [display_name, taxonomy, " ".join(tags)]))

    def _reindex_identity(self, identity: IdentityFacet) -> None:
        """Refresh the FTS row for one identity (caller holds the transaction)."""
        self.conn.execute("DELETE FROM identity_fts WHERE asset_id = ?",
                          (str(identity.asset_id),))
        text = self._identity_text(identity.display_name, identity.taxonomy, identity.tags)
        if text:
            self.conn.execute("INSERT INTO identity_fts (asset_id, text) VALUES (?, ?)",
                              (str(identity.asset_id), text))

    def close(self) -> None:
        self.conn.close()

    # --- identity ---
    def create_asset(self, asset: Asset, identity: IdentityFacet) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO asset (id, lifecycle, asset_type, created_at, created_by, origin)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (str(asset.id), asset.lifecycle.value, asset.asset_type,
                 _dt(asset.created_at), asset.created_by, json.dumps(asset.origin)),
            )
            self.conn.execute(
                "INSERT INTO facet_identity (asset_id, display_name, taxonomy, status, tags, attributes)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (str(identity.asset_id), identity.display_name, identity.taxonomy,
                 identity.status, json.dumps(identity.tags), json.dumps(identity.attributes)),
            )
            self._reindex_identity(identity)

    def get_asset(self, asset_id: UUID) -> Asset | None:
        row = self.conn.execute("SELECT * FROM asset WHERE id = ?", (str(asset_id),)).fetchone()
        if row is None:
            return None
        return Asset(
            asset_type=row["asset_type"],
            created_by=row["created_by"],
            id=UUID(row["id"]),
            lifecycle=Lifecycle(row["lifecycle"]),
            origin=json.loads(row["origin"]),
            created_at=_parse_dt(row["created_at"]),
        )

    def list_assets(self, asset_type: str | None = None,
                    lifecycle: Lifecycle | None = None,
                    created_by: str | None = None) -> list[Asset]:
        sql, params = "SELECT * FROM asset", []
        clauses = []
        if asset_type is not None:
            clauses.append("asset_type = ?")
            params.append(asset_type)
        if lifecycle is not None:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle(lifecycle).value)
        if created_by is not None:
            clauses.append("created_by = ?")
            params.append(created_by)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [self._row_to_asset(r) for r in self.conn.execute(sql, params).fetchall()]

    @staticmethod
    def _row_to_asset(r: sqlite3.Row) -> Asset:
        return Asset(
            asset_type=r["asset_type"], created_by=r["created_by"], id=UUID(r["id"]),
            lifecycle=Lifecycle(r["lifecycle"]), origin=json.loads(r["origin"]),
            created_at=_parse_dt(r["created_at"]),
        )

    def _in_clause(self, asset_ids: list[UUID]) -> tuple[str, list[str]]:
        placeholders = ",".join("?" * len(asset_ids))
        return placeholders, [str(a) for a in asset_ids]

    def identities(self, asset_ids: list[UUID]) -> dict[UUID, IdentityFacet]:
        if not asset_ids:
            return {}
        placeholders, params = self._in_clause(asset_ids)
        rows = self.conn.execute(
            f"SELECT * FROM facet_identity WHERE asset_id IN ({placeholders})", params).fetchall()
        return {UUID(r["asset_id"]): IdentityFacet(
            asset_id=UUID(r["asset_id"]), display_name=r["display_name"], taxonomy=r["taxonomy"],
            status=r["status"], tags=json.loads(r["tags"]), attributes=json.loads(r["attributes"]),
        ) for r in rows}

    def latest_sources(self, asset_ids: list[UUID]) -> dict[UUID, SourceVersion]:
        if not asset_ids:
            return {}
        placeholders, params = self._in_clause(asset_ids)
        rows = self.conn.execute(
            f"SELECT * FROM facet_source_version WHERE is_latest=1 AND asset_id IN ({placeholders})",
            params).fetchall()
        return {UUID(r["asset_id"]): SourceVersion(
            asset_id=UUID(r["asset_id"]), location_uri=r["location_uri"], tool=r["tool"],
            revision=r["revision"], version_num=r["version_num"], is_latest=bool(r["is_latest"]),
            published_by=r["published_by"], published_at=_parse_dt(r["published_at"]),
        ) for r in rows}

    def latest_runtimes(self, asset_ids: list[UUID]) -> dict[UUID, RuntimeVersion]:
        if not asset_ids:
            return {}
        placeholders, params = self._in_clause(asset_ids)
        rows = self.conn.execute(
            f"SELECT * FROM facet_runtime_version WHERE is_latest=1 AND asset_id IN ({placeholders})",
            params).fetchall()
        return {UUID(r["asset_id"]): RuntimeVersion(
            asset_id=UUID(r["asset_id"]), location_uri=r["location_uri"], build_id=r["build_id"],
            version_num=r["version_num"], is_latest=bool(r["is_latest"]), cooked_at=_parse_dt(r["cooked_at"]),
        ) for r in rows}

    def search_candidates(self, query: str, asset_type: str | None = None,
                          limit: int = 200) -> list[tuple[Asset, IdentityFacet]]:
        tokens = re.findall(r"[a-z0-9]+", query.lower())
        if not tokens:
            return []
        # quoted tokens OR-joined: any shared token surfaces the row (the narrowing
        # contract, rules.is_similarity_candidate); the verb re-ranks the rest.
        match = " OR ".join(f'"{t}"' for t in tokens)
        sql = ("SELECT a.*, fi.* FROM identity_fts f"
               " JOIN asset a ON a.id = f.asset_id"
               " JOIN facet_identity fi ON fi.asset_id = f.asset_id"
               " WHERE identity_fts MATCH ?")
        params: list = [match]
        if asset_type is not None:
            sql += " AND a.asset_type = ?"
            params.append(asset_type)
        sql += " LIMIT ?"
        params.append(limit)
        out = []
        for r in self.conn.execute(sql, params).fetchall():
            out.append((self._row_to_asset(r), IdentityFacet(
                asset_id=UUID(r["asset_id"]), display_name=r["display_name"],
                taxonomy=r["taxonomy"], status=r["status"], tags=json.loads(r["tags"]),
                attributes=json.loads(r["attributes"]))))
        return out

    def get_identity(self, asset_id: UUID) -> IdentityFacet | None:
        row = self.conn.execute(
            "SELECT * FROM facet_identity WHERE asset_id = ?", (str(asset_id),)).fetchone()
        if row is None:
            return None
        return IdentityFacet(
            asset_id=UUID(row["asset_id"]),
            display_name=row["display_name"],
            taxonomy=row["taxonomy"],
            status=row["status"],
            tags=json.loads(row["tags"]),
            attributes=json.loads(row["attributes"]),
        )

    def save_identity(self, identity: IdentityFacet) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE facet_identity SET display_name=?, taxonomy=?, status=?, tags=?, attributes=?"
                " WHERE asset_id=?",
                (identity.display_name, identity.taxonomy, identity.status,
                 json.dumps(identity.tags), json.dumps(identity.attributes), str(identity.asset_id)),
            )
            self._reindex_identity(identity)

    def set_lifecycle(self, asset_id: UUID, lifecycle: Lifecycle) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE asset SET lifecycle=? WHERE id=?",
                (Lifecycle(lifecycle).value, str(asset_id)),
            )

    @staticmethod
    def _as_version_conflict(exc: sqlite3.IntegrityError) -> None:
        # a UNIQUE(asset_id, version_num) or one_latest_* violation means another
        # writer advanced the version concurrently -> retryable VersionConflict.
        # FK / other integrity errors are real bugs; re-raise them untouched.
        if "unique" in str(exc).lower():
            raise VersionConflict(str(exc)) from exc
        raise exc

    # --- source facet ---
    def add_source_version(self, v: SourceVersion) -> None:
        try:
            with self.conn:   # demote + insert atomically -> one_latest_source holds
                self.conn.execute(
                    "UPDATE facet_source_version SET is_latest=0 WHERE asset_id=? AND is_latest=1",
                    (str(v.asset_id),))
                self.conn.execute(
                    "INSERT INTO facet_source_version"
                    " (asset_id, location_uri, tool, revision, version_num, is_latest, published_at, published_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(v.asset_id), v.location_uri, v.tool, v.revision, v.version_num,
                     1 if v.is_latest else 0, _dt(v.published_at), v.published_by),
                )
        except sqlite3.IntegrityError as exc:
            self._as_version_conflict(exc)

    def update_source_location(self, asset_id: UUID, new_location_uri: str,
                               new_revision: str | None = None) -> bool:
        with self.conn:   # in-place location update of the latest version (a move, not a version)
            if new_revision is None:
                cur = self.conn.execute(
                    "UPDATE facet_source_version SET location_uri=?"
                    " WHERE asset_id=? AND is_latest=1", (new_location_uri, str(asset_id)))
            else:
                cur = self.conn.execute(
                    "UPDATE facet_source_version SET location_uri=?, revision=?"
                    " WHERE asset_id=? AND is_latest=1",
                    (new_location_uri, str(new_revision), str(asset_id)))
            return cur.rowcount > 0

    def source_versions(self, asset_id: UUID) -> list[SourceVersion]:
        rows = self.conn.execute(
            "SELECT * FROM facet_source_version WHERE asset_id=? ORDER BY version_num",
            (str(asset_id),)).fetchall()
        return [
            SourceVersion(
                asset_id=UUID(r["asset_id"]), location_uri=r["location_uri"], tool=r["tool"],
                revision=r["revision"], version_num=r["version_num"], is_latest=bool(r["is_latest"]),
                published_by=r["published_by"], published_at=_parse_dt(r["published_at"]),
            )
            for r in rows
        ]

    # --- runtime facet ---
    def add_runtime_version(self, v: RuntimeVersion) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE facet_runtime_version SET is_latest=0 WHERE asset_id=? AND is_latest=1",
                    (str(v.asset_id),))
                self.conn.execute(
                    "INSERT INTO facet_runtime_version"
                    " (asset_id, location_uri, build_id, version_num, is_latest, cooked_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (str(v.asset_id), v.location_uri, v.build_id, v.version_num,
                     1 if v.is_latest else 0, _dt(v.cooked_at)),
                )
        except sqlite3.IntegrityError as exc:
            self._as_version_conflict(exc)

    def update_runtime_location(self, asset_id: UUID, new_location_uri: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE facet_runtime_version SET location_uri=?"
                " WHERE asset_id=? AND is_latest=1", (new_location_uri, str(asset_id)))
            return cur.rowcount > 0

    def runtime_versions(self, asset_id: UUID) -> list[RuntimeVersion]:
        rows = self.conn.execute(
            "SELECT * FROM facet_runtime_version WHERE asset_id=? ORDER BY version_num",
            (str(asset_id),)).fetchall()
        return [
            RuntimeVersion(
                asset_id=UUID(r["asset_id"]), location_uri=r["location_uri"], build_id=r["build_id"],
                version_num=r["version_num"], is_latest=bool(r["is_latest"]), cooked_at=_parse_dt(r["cooked_at"]),
            )
            for r in rows
        ]

    # --- relationships ---
    def add_relationship(self, r: Relationship) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO relationship"
                    " (from_asset, to_asset, rel_type, binding_mode, pinned_version, attributes)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    self._rel_params(r),
                )
        except sqlite3.IntegrityError as exc:
            # only the UNIQUE(from,to,rel_type) violation means "edge already exists";
            # re-raise FK / other integrity errors so real data bugs aren't masked.
            if "unique" not in str(exc).lower():
                raise
            raise ValueError(
                f"edge already exists: {r.from_asset}-{r.rel_type.value}->{r.to_asset}"
            ) from exc

    def upsert_relationship(self, r: Relationship) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO relationship"
                " (from_asset, to_asset, rel_type, binding_mode, pinned_version, attributes)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (from_asset, to_asset, rel_type) DO UPDATE SET"
                "   binding_mode=excluded.binding_mode,"
                "   pinned_version=excluded.pinned_version,"
                "   attributes=excluded.attributes",
                self._rel_params(r),
            )

    @staticmethod
    def _rel_params(r: Relationship) -> tuple:
        return (
            str(r.from_asset), str(r.to_asset), r.rel_type.value,
            r.binding_mode.value if r.binding_mode is not None else None,
            r.pinned_version, json.dumps(r.attributes),
        )

    def edges_from(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]:
        return self._edges("from_asset", asset_id, rel_type)

    def edges_to(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]:
        return self._edges("to_asset", asset_id, rel_type)

    def _edges(self, column: str, asset_id: UUID, rel_type: RelType | None) -> list[Relationship]:
        sql = f"SELECT * FROM relationship WHERE {column}=?"
        params: tuple = (str(asset_id),)
        if rel_type is not None:
            sql += " AND rel_type=?"
            params += (RelType(rel_type).value,)
        return [self._row_to_rel(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_edge(self, frm: UUID, to: UUID, rel_type: RelType) -> Relationship | None:
        row = self.conn.execute(
            "SELECT * FROM relationship WHERE from_asset=? AND to_asset=? AND rel_type=?",
            (str(frm), str(to), RelType(rel_type).value)).fetchone()
        return self._row_to_rel(row) if row is not None else None

    @staticmethod
    def _row_to_rel(r: sqlite3.Row) -> Relationship:
        return Relationship(
            from_asset=UUID(r["from_asset"]), to_asset=UUID(r["to_asset"]),
            rel_type=RelType(r["rel_type"]),
            binding_mode=BindingMode(r["binding_mode"]) if r["binding_mode"] is not None else None,
            pinned_version=r["pinned_version"], attributes=json.loads(r["attributes"]),
        )
