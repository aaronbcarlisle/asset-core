from __future__ import annotations
import json
import logging
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta

from assetcore.sdk.hub import HubContext, PipelineConfig

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_type TEXT,
    status TEXT,
    created_by TEXT,
    taxonomy TEXT,
    updated_at TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    binding_mode TEXT NOT NULL DEFAULT 'float',
    PRIMARY KEY (from_id, to_id, rel_type)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


_ASSETS_COLUMNS = ("id", "name", "asset_type", "status", "created_by",
                   "taxonomy", "updated_at", "payload_json")


def open_replica(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)     # creates missing tables at the CURRENT schema
    _migrate_assets(conn)           # upgrade a pre-existing cache in place
    return conn


def _migrate_assets(conn: sqlite3.Connection) -> None:
    """Bring an older replica's `assets` table up to the current schema in place.

    A cache created by an earlier version lacks the extracted `taxonomy` column and
    has `asset_type NOT NULL`; `upsert_asset` would then fail ("no such column" or a
    NOT NULL violation on offline/partial records). Rebuild the table preserving
    overlapping rows so an existing hub cache upgrades without a manual delete /
    full re-hydrate. (The replica is a cache; the outbox is the durable queue.)
    """
    info = conn.execute("PRAGMA table_info(assets)").fetchall()
    if not info:
        return
    cols = {r["name"] for r in info}
    asset_type_not_null = any(r["name"] == "asset_type" and r["notnull"] == 1 for r in info)
    if "taxonomy" in cols and not asset_type_not_null:
        return   # already current — no rebuild
    carried = [c for c in _ASSETS_COLUMNS if c in cols]
    collist = ", ".join(carried)
    with conn:
        conn.execute("ALTER TABLE assets RENAME TO _assets_legacy")
        conn.executescript(
            "CREATE TABLE assets ("
            " id TEXT PRIMARY KEY, name TEXT NOT NULL, asset_type TEXT, status TEXT,"
            " created_by TEXT, taxonomy TEXT, updated_at TEXT, payload_json TEXT NOT NULL);")
        conn.execute(f"INSERT INTO assets ({collist}) SELECT {collist} FROM _assets_legacy")
        conn.execute("DROP TABLE _assets_legacy")


def _record_updated_at(record: dict) -> str | None:
    """The 'last touched' timestamp for incremental hydrate, mirroring the central
    service's list_assets `updated_since` math: max of created_at, the latest
    source's published_at, and the latest runtime's cooked_at."""
    stamps = [record.get("created_at")]
    src = record.get("source") or {}
    stamps.append(src.get("published_at"))
    rt = record.get("runtime") or {}
    stamps.append(rt.get("cooked_at"))
    present = [s for s in stamps if s]
    return max(present) if present else None


def upsert_asset(conn: sqlite3.Connection, asset: dict) -> None:
    """Store a resolve-shaped record. The full record rides in payload_json (so the
    local reader can serve the exact central shapes); the filterable fields
    (created_by, taxonomy, updated_at) are extracted into columns."""
    identity = asset.get("identity") or {}
    meta = asset.get("meta") or {}
    name = identity.get("display_name") or asset.get("name") or asset["id"]
    asset_type = asset.get("asset_type") or meta.get("asset_type")
    status = identity.get("status") if identity else asset.get("status")
    created_by = asset.get("created_by") or meta.get("created_by")
    taxonomy = identity.get("taxonomy")
    updated_at = asset.get("updated_at") or _record_updated_at(asset)
    conn.execute(
        "INSERT INTO assets (id, name, asset_type, status, created_by, taxonomy, updated_at, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, asset_type=excluded.asset_type, "
        "status=excluded.status, created_by=excluded.created_by, taxonomy=excluded.taxonomy, "
        "updated_at=excluded.updated_at, payload_json=excluded.payload_json",
        (asset["id"], name, asset_type, status, created_by, taxonomy, updated_at, json.dumps(asset)),
    )


def upsert_relation(conn: sqlite3.Connection, from_id: str, to_id: str,
                    rel_type: str, binding_mode: str = "float") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO relations (from_id, to_id, rel_type, binding_mode) VALUES (?, ?, ?, ?)",
        (from_id, to_id, rel_type, binding_mode),
    )


def get_asset(conn: sqlite3.Connection, asset_id: str) -> dict | None:
    row = conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_id,)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def _atomic_replace(src: str, dst: str) -> None:
    """os.replace with retry for Windows file-lock races (3 attempts, 200ms apart)."""
    last_err: OSError | None = None
    for attempt in range(3):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            last_err = exc
            if attempt < 2:
                time.sleep(0.2)
    logger.error("atomic replica swap failed after 3 attempts: %s -> %s", src, dst)
    raise last_err  # type: ignore[misc]


_HYDRATE_PAGE = 500


def _list_all(client, **filters) -> list[dict]:
    """Page through client.list_assets() to completion — /assets defaults to
    limit=500, so a single call would silently truncate a large catalog."""
    out: list[dict] = []
    offset = 0
    while True:
        page = client.list_assets(limit=_HYDRATE_PAGE, offset=offset, **filters)
        out.extend(page)
        if len(page) < _HYDRATE_PAGE:
            return out
        offset += _HYDRATE_PAGE


def hydrate_cache(pipeline: PipelineConfig, ctx: HubContext, client, now_iso: str) -> dict:
    project = pipeline.scope.get("assetcore_project", "")
    prefix = project
    since = (datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - timedelta(days=pipeline.recent_days)).isoformat()
    # seed set: user's authored assets + recently touched (spec §5.2 steps 1),
    # paged so catalogs larger than one /assets page are fully hydrated.
    seeds = {a["id"]: a for a in _list_all(client, created_by=ctx["user_name"], taxonomy_prefix=prefix)}
    for a in _list_all(client, taxonomy_prefix=prefix, updated_since=since):
        seeds.setdefault(a["id"], a)
    # transitive dependency closure via BFS (spec §5.2 step 2) — edges from dependencies()
    tmp = pipeline.local_cache + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = open_replica(tmp)
    assets = 0
    relations = 0
    seen: set[str] = set()
    queue = deque(seeds)
    while queue:
        aid = queue.popleft()
        if aid in seen:
            continue
        seen.add(aid)
        resolved = client.resolve(aid)
        if resolved is None:
            continue
        # Build the record the local reader serves: the central resolve() shape
        # (id/meta/identity/source/runtime), enriched with created_at from the
        # list summary (resolve's meta carries no created_at). This is exactly what
        # /resolve returns, and /assets is a projection of it — so local answers are
        # shape-identical to central.
        summary = seeds.get(aid, {})
        record = {
            "id": aid,
            "asset_type": (resolved.get("meta") or {}).get("asset_type") or summary.get("asset_type"),
            "created_by": (resolved.get("meta") or {}).get("created_by") or summary.get("created_by"),
            "created_at": summary.get("created_at"),
            "meta": resolved.get("meta"),
            "identity": resolved.get("identity"),
            "source": resolved.get("source"),
            "runtime": resolved.get("runtime"),
        }
        upsert_asset(conn, record)
        assets += 1
        for dep in client.dependencies(aid):
            to_id = dep["asset_id"]
            upsert_relation(conn, aid, to_id, dep["rel_type"])
            relations += 1
            if to_id not in seen:
                queue.append(to_id)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('hydrated_at', ?)", (now_iso,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('project', ?)", (project,))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    _atomic_replace(tmp, pipeline.local_cache)
    return {"assets": assets, "relations": relations, "hydrated_at": now_iso}
