from __future__ import annotations
import json
import logging
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from assetcore.sdk.hub import HubContext, PipelineConfig

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    status TEXT,
    created_by TEXT,
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


def open_replica(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def upsert_asset(conn: sqlite3.Connection, asset: dict) -> None:
    conn.execute(
        "INSERT INTO assets (id, name, asset_type, status, created_by, updated_at, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, asset_type=excluded.asset_type, "
        "status=excluded.status, created_by=excluded.created_by, "
        "updated_at=excluded.updated_at, payload_json=excluded.payload_json",
        (asset["id"], asset.get("name", asset["id"]), asset.get("asset_type"), asset.get("status"),
         asset.get("created_by"), asset.get("updated_at"), json.dumps(asset)),
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


def hydrate_cache(pipeline: PipelineConfig, ctx: HubContext, client, now_iso: str) -> dict:
    project = pipeline.scope.get("assetcore_project", "")
    prefix = project
    since = (datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - timedelta(days=pipeline.recent_days)).isoformat()
    # seed set: user's authored assets + recently touched (spec §5.2 steps 1)
    seeds = {a["id"]: a for a in client.list_assets(created_by=ctx["user_name"], taxonomy_prefix=prefix)}
    for a in client.list_assets(taxonomy_prefix=prefix, updated_since=since):
        seeds.setdefault(a["id"], a)
    # transitive dependency closure via BFS (spec §5.2 step 2) — edges from dependents()
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
        # flatten resolve response + seed fields for replica storage
        row = seeds.get(aid, {})
        row.update({"id": aid, "name": (resolved.get("identity") or {}).get("display_name") or aid,
                    "asset_type": (resolved.get("meta") or {}).get("asset_type")})
        upsert_asset(conn, row)
        assets += 1
        for dep in client.dependents(aid):
            upsert_relation(conn, aid, dep["to_id"], dep["rel_type"], dep.get("binding_mode", "float"))
            relations += 1
            if dep["to_id"] not in seen:
                queue.append(dep["to_id"])
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('hydrated_at', ?)", (now_iso,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('project', ?)", (project,))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    _atomic_replace(tmp, pipeline.local_cache)
    return {"assets": assets, "relations": relations, "hydrated_at": now_iso}
