"""PostgresRepo — an AssetRepo backed by PostgreSQL via psycopg2.

The production storage target. Mirrors SqliteRepo against the SAME canonical
infra/schema.sql (used here in its native PG dialect). psycopg2 is imported
lazily so SQLite/in-memory users need no driver, and the contract is identical:
same ports, same one-latest-on-write invariant, same UNIQUE-edge guard.

NOTE: this backend is code-complete but exercised only when a Postgres instance
is available — tests/integration skips it unless ASSETCORE_TEST_DSN is set and
psycopg2 is installed. Everything below mirrors the SQLite implementation that IS
covered, so the port contract is what's really being proven.
"""
import pathlib
import re
from uuid import UUID

from assetcore.core.entities import (
    Asset,
    IdentityFacet,
    Relationship,
    RuntimeVersion,
    SourceVersion,
)
from assetcore.core.types import BindingMode, Lifecycle, RelType

_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"
# We always supply ids explicitly, so the pgcrypto extension is unnecessary
# (and may need privileges we don't have). Strip it from the bootstrap DDL.
_BOOTSTRAP_DDL = re.sub(
    r"CREATE EXTENSION.*?;", "", _SCHEMA_PATH.read_text(), flags=re.S)

_TABLES = (
    "event", "relationship", "facet_runtime_version",
    "facet_source_version", "facet_identity", "asset",
)


class PostgresRepo:
    """Satisfies core.ports.AssetRepo."""

    def __init__(self, dsn: str) -> None:
        import psycopg2
        import psycopg2.extras
        self._psycopg2 = psycopg2
        self._Json = psycopg2.extras.Json
        self._dict_cursor = psycopg2.extras.RealDictCursor
        psycopg2.extras.register_uuid()          # UUID <-> Python uuid.UUID
        self.conn = psycopg2.connect(dsn)
        with self.conn, self.conn.cursor() as cur:
            cur.execute(_BOOTSTRAP_DDL)

    def close(self) -> None:
        self.conn.close()

    def reset(self) -> None:
        """Truncate every table — used by the integration suite for isolation."""
        with self.conn, self.conn.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE")

    # --- identity ---
    def create_asset(self, asset: Asset, identity: IdentityFacet) -> None:
        with self.conn, self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO asset (id, lifecycle, asset_type, created_at, created_by, origin)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (asset.id, asset.lifecycle.value, asset.asset_type,
                 asset.created_at, asset.created_by, self._Json(asset.origin)),
            )
            cur.execute(
                "INSERT INTO facet_identity (asset_id, display_name, taxonomy, status, tags, attributes)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (identity.asset_id, identity.display_name, identity.taxonomy,
                 identity.status, identity.tags, self._Json(identity.attributes)),
            )

    def get_asset(self, asset_id: UUID) -> Asset | None:
        row = self._one("SELECT * FROM asset WHERE id = %s", (asset_id,))
        if row is None:
            return None
        return Asset(
            asset_type=row["asset_type"], created_by=row["created_by"], id=row["id"],
            lifecycle=Lifecycle(row["lifecycle"]), origin=row["origin"], created_at=row["created_at"],
        )

    def get_identity(self, asset_id: UUID) -> IdentityFacet | None:
        row = self._one("SELECT * FROM facet_identity WHERE asset_id = %s", (asset_id,))
        if row is None:
            return None
        return IdentityFacet(
            asset_id=row["asset_id"], display_name=row["display_name"], taxonomy=row["taxonomy"],
            status=row["status"], tags=list(row["tags"]), attributes=row["attributes"],
        )

    def save_identity(self, identity: IdentityFacet) -> None:
        with self.conn, self.conn.cursor() as cur:
            cur.execute(
                "UPDATE facet_identity SET display_name=%s, taxonomy=%s, status=%s, tags=%s, attributes=%s"
                " WHERE asset_id=%s",
                (identity.display_name, identity.taxonomy, identity.status,
                 identity.tags, self._Json(identity.attributes), identity.asset_id),
            )

    def set_lifecycle(self, asset_id: UUID, lifecycle: Lifecycle) -> None:
        with self.conn, self.conn.cursor() as cur:
            cur.execute("UPDATE asset SET lifecycle=%s WHERE id=%s",
                        (Lifecycle(lifecycle).value, asset_id))

    # --- source facet ---
    def add_source_version(self, v: SourceVersion) -> None:
        with self.conn, self.conn.cursor() as cur:   # demote + insert in one tx
            cur.execute(
                "UPDATE facet_source_version SET is_latest=FALSE WHERE asset_id=%s AND is_latest",
                (v.asset_id,))
            cur.execute(
                "INSERT INTO facet_source_version"
                " (asset_id, location_uri, tool, revision, version_num, is_latest, published_at, published_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (v.asset_id, v.location_uri, v.tool, v.revision, v.version_num,
                 v.is_latest, v.published_at, v.published_by),
            )

    def source_versions(self, asset_id: UUID) -> list[SourceVersion]:
        rows = self._all(
            "SELECT * FROM facet_source_version WHERE asset_id=%s ORDER BY version_num", (asset_id,))
        return [
            SourceVersion(
                asset_id=r["asset_id"], location_uri=r["location_uri"], tool=r["tool"],
                revision=r["revision"], version_num=r["version_num"], is_latest=r["is_latest"],
                published_by=r["published_by"], published_at=r["published_at"],
            )
            for r in rows
        ]

    # --- runtime facet ---
    def add_runtime_version(self, v: RuntimeVersion) -> None:
        with self.conn, self.conn.cursor() as cur:
            cur.execute(
                "UPDATE facet_runtime_version SET is_latest=FALSE WHERE asset_id=%s AND is_latest",
                (v.asset_id,))
            cur.execute(
                "INSERT INTO facet_runtime_version"
                " (asset_id, location_uri, build_id, version_num, is_latest, cooked_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (v.asset_id, v.location_uri, v.build_id, v.version_num, v.is_latest, v.cooked_at),
            )

    def runtime_versions(self, asset_id: UUID) -> list[RuntimeVersion]:
        rows = self._all(
            "SELECT * FROM facet_runtime_version WHERE asset_id=%s ORDER BY version_num", (asset_id,))
        return [
            RuntimeVersion(
                asset_id=r["asset_id"], location_uri=r["location_uri"], build_id=r["build_id"],
                version_num=r["version_num"], is_latest=r["is_latest"], cooked_at=r["cooked_at"],
            )
            for r in rows
        ]

    # --- relationships ---
    def add_relationship(self, r: Relationship) -> None:
        try:
            with self.conn, self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO relationship"
                    " (from_asset, to_asset, rel_type, binding_mode, pinned_version, attributes)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    self._rel_params(r),
                )
        except self._psycopg2.IntegrityError as exc:
            raise ValueError(
                f"edge already exists: {r.from_asset}-{r.rel_type.value}->{r.to_asset}"
            ) from exc

    def upsert_relationship(self, r: Relationship) -> None:
        with self.conn, self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO relationship"
                " (from_asset, to_asset, rel_type, binding_mode, pinned_version, attributes)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (from_asset, to_asset, rel_type) DO UPDATE SET"
                "   binding_mode=excluded.binding_mode,"
                "   pinned_version=excluded.pinned_version,"
                "   attributes=excluded.attributes",
                self._rel_params(r),
            )

    def _rel_params(self, r: Relationship) -> tuple:
        return (
            r.from_asset, r.to_asset, r.rel_type.value,
            r.binding_mode.value if r.binding_mode is not None else None,
            r.pinned_version, self._Json(r.attributes),
        )

    def edges_from(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]:
        return self._edges("from_asset", asset_id, rel_type)

    def edges_to(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]:
        return self._edges("to_asset", asset_id, rel_type)

    def _edges(self, column: str, asset_id: UUID, rel_type: RelType | None) -> list[Relationship]:
        sql = f"SELECT * FROM relationship WHERE {column}=%s"
        params: tuple = (asset_id,)
        if rel_type is not None:
            sql += " AND rel_type=%s"
            params += (RelType(rel_type).value,)
        return [self._row_to_rel(r) for r in self._all(sql, params)]

    def get_edge(self, frm: UUID, to: UUID, rel_type: RelType) -> Relationship | None:
        row = self._one(
            "SELECT * FROM relationship WHERE from_asset=%s AND to_asset=%s AND rel_type=%s",
            (frm, to, RelType(rel_type).value))
        return self._row_to_rel(row) if row is not None else None

    @staticmethod
    def _row_to_rel(r) -> Relationship:
        return Relationship(
            from_asset=r["from_asset"], to_asset=r["to_asset"], rel_type=RelType(r["rel_type"]),
            binding_mode=BindingMode(r["binding_mode"]) if r["binding_mode"] is not None else None,
            pinned_version=r["pinned_version"], attributes=r["attributes"],
        )

    # --- query helpers ---
    def _one(self, sql: str, params: tuple):
        with self.conn.cursor(cursor_factory=self._dict_cursor) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: tuple) -> list:
        with self.conn.cursor(cursor_factory=self._dict_cursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
