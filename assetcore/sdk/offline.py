from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Mapping, TypedDict

import httpx

from assetcore.sdk.hub import PipelineConfig
from assetcore.sdk import replica as _replica

_PROBE_TIMEOUT = 2.0


class OutboxEntry(TypedDict):
    id: int
    verb: str
    payload: dict
    asset_id: str | None
    created_at: str
    status: str
    error: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verb TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    asset_id TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT
);
"""


def open_outbox(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def enqueue(conn: sqlite3.Connection, verb: str, payload: dict,
            asset_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO outbox (verb, payload_json, asset_id, created_at) VALUES (?, ?, ?, ?)",
        (verb, json.dumps(payload), asset_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return int(cur.lastrowid)


def _count(conn: sqlite3.Connection, status: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM outbox WHERE status=?", (status,)).fetchone()
    return int(row["c"])


def pending_count(conn: sqlite3.Connection) -> int:
    return _count(conn, "pending")


def failed_count(conn: sqlite3.Connection) -> int:
    return _count(conn, "failed")


def _row_to_entry(r: sqlite3.Row) -> OutboxEntry:
    return {
        "id": r["id"], "verb": r["verb"], "payload": json.loads(r["payload_json"]),
        "asset_id": r["asset_id"], "created_at": r["created_at"],
        "status": r["status"], "error": r["error"],
    }


def list_pending(conn: sqlite3.Connection) -> list[OutboxEntry]:
    rows = conn.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY id").fetchall()
    return [_row_to_entry(r) for r in rows]


def mark_done(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("UPDATE outbox SET status='done', error=NULL WHERE id=?", (entry_id,))
    conn.commit()


def mark_failed(conn: sqlite3.Connection, entry_id: int, error: str) -> None:
    conn.execute("UPDATE outbox SET status='failed', error=? WHERE id=?", (error[:2000], entry_id))
    conn.commit()


def retry_failed(conn: sqlite3.Connection) -> int:
    cur = conn.execute("UPDATE outbox SET status='pending', error=NULL WHERE status='failed'")
    conn.commit()
    return cur.rowcount


def list_failed(conn: sqlite3.Connection) -> list[OutboxEntry]:
    rows = conn.execute("SELECT * FROM outbox WHERE status='failed' ORDER BY id").fetchall()
    return [_row_to_entry(r) for r in rows]


def _already_applied(client, entry: OutboxEntry) -> bool:
    """Read central state; True if it already reflects this entry (spec §5.3, §7.5.4).

    Idempotency is EXACT-MATCH, never ordered: revisions are opaque strings (P4 CLs,
    git shas) with no reliable ordering — the old ``>=`` comparison was doubly wrong
    (``"9" >= "10"`` is True, and a git sha has no order at all). So an entry counts
    as already-applied only when central's current state equals exactly what this
    entry would write. A queued write against a DIFFERENT current revision is NOT
    skipped — it is dispatched and lands as a new (monotonic, auditable) version
    rather than being silently dropped.
    """
    p, verb = entry["payload"], entry["verb"]
    try:
        if verb == "declare":
            aid = p.get("id")
            return bool(aid and client.resolve(aid) is not None)
        if verb == "bind_source":
            cur = client.get_source(p["asset_id"])
            return bool(cur and cur["location_uri"] == p["location_uri"]
                        and str(cur["revision"]) == str(p["revision"]))
        if verb == "relocate":
            cur = client.get_source(p["asset_id"])
            return bool(cur and cur["location_uri"] == p["new_location_uri"]
                        and (p.get("new_revision") is None
                             or str(cur.get("revision")) == str(p["new_revision"])))
        # relate/rename/bulk carry no cheaply-checkable "already there" signal;
        # a re-applied relate that central already has surfaces as a duplicate-edge
        # error at dispatch, which replay_outbox treats as already-applied.
        return False
    except Exception:
        return False


def _is_duplicate_edge_error(exc: Exception) -> bool:
    """True when a dispatch failed only because the edge already exists on central.

    A relate replayed after it already reached central (e.g. a crash between the
    central write and mark_done) comes back as HTTP 400/409 whose body names a
    duplicate edge. That is success-from-our-side, not a failure to hold the queue
    on — so replay treats it as already-applied instead of poisoning the outbox.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return False
    try:
        if response.status_code not in (400, 409):
            return False
        body = response.text.lower()
    except Exception:
        return False
    return "duplicate edge" in body or "edge already exists" in body


def _dispatch(client, entry: OutboxEntry) -> None:
    p, verb = entry["payload"], entry["verb"]
    if verb == "declare":
        client.declare(p["asset_type"], p["created_by"], origin=p.get("origin"), asset_id=p.get("id"))
    elif verb == "bind_source":
        client.bind_source(p["asset_id"], p["location_uri"], p["tool"], p["revision"], p["published_by"])
    elif verb == "relate":
        client.relate(p["from_asset"], p["to_asset"], p["rel_type"],
                      binding_mode=p.get("binding_mode"), actor=p.get("actor"))
    elif verb == "rename":
        client.rename(p["asset_id"], p["new_name"], p["actor"], p.get("new_taxonomy"))
    elif verb == "relocate":
        client.relocate(p["asset_id"], p["new_location_uri"], p["actor"],
                        facet=p.get("facet", "source"), new_revision=p.get("new_revision"))
    elif verb == "bulk_relocate":
        client.bulk_relocate(p["moves"])
    else:
        raise ValueError(f"unknown outbox verb: {verb}")


def replay_outbox(conn: sqlite3.Connection, client) -> dict:
    replayed = failed = skipped = held = 0
    failed_assets: set[str] = set()
    for entry in list_pending(conn):
        aid = entry["asset_id"]
        if aid and aid in failed_assets:
            held += 1
            continue
        try:
            if _already_applied(client, entry):
                mark_done(conn, entry["id"])
                skipped += 1
                continue
            _dispatch(client, entry)
            mark_done(conn, entry["id"])
            replayed += 1
        except Exception as exc:
            if _is_duplicate_edge_error(exc):   # already on central (crash-after-write)
                mark_done(conn, entry["id"])
                skipped += 1
                continue
            mark_failed(conn, entry["id"], repr(exc))
            failed += 1
            if aid:
                failed_assets.add(aid)
    return {"replayed": replayed, "failed": failed, "skipped": skipped, "held": held}


def central_is_up(central) -> bool:
    """Cheap probe: ping() when present, else GET /health on AssetcoreClient."""
    if central is None:
        return False
    try:
        if hasattr(central, "ping"):
            central.ping()
        elif hasattr(central, "_get"):
            central._get("/health")
        else:
            return False
        return True
    except Exception:
        return False


class HybridClient:
    """Spec §7.3: local-first reads; central-else-outbox writes with optimistic patch."""

    def __init__(self, pipeline: PipelineConfig, central,
                 os_env: Mapping[str, str] | None = None):
        env = os.environ if os_env is None else os_env
        self._pipeline = pipeline
        self._central = central
        self._local_url = env.get("ASSETCORE_LOCAL_URL",
                                  f"http://127.0.0.1:{pipeline.local_reader_port}")
        self._actor_project = pipeline.scope.get("assetcore_project", "")

    # ---- reads: local reader first, central fallback ----

    def _local_get(self, path: str, params: dict | None = None):
        r = httpx.get(self._local_url + path, params=params, timeout=_PROBE_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def resolve(self, asset_id: str) -> dict | None:
        try:
            return self._local_get(f"/resolve/{asset_id}")
        except Exception:
            return self._central.resolve(asset_id)

    def dependents(self, asset_id: str) -> list[dict]:
        try:
            return self._local_get(f"/dependents/{asset_id}")
        except Exception:
            return self._central.dependents(asset_id)

    def list_assets(self, created_by: str | None = None, taxonomy_prefix: str | None = None,
                    updated_since: str | None = None) -> list[dict]:
        params = {k: v for k, v in {"created_by": created_by, "taxonomy_prefix": taxonomy_prefix,
                                    "updated_since": updated_since}.items() if v}
        try:
            return self._local_get("/assets", params=params or None)
        except Exception:
            return self._central.list_assets(created_by=created_by, taxonomy_prefix=taxonomy_prefix,
                                             updated_since=updated_since)

    # ---- writes: central if up, else outbox + optimistic replica patch ----

    def _outbox(self):
        return open_outbox(self._pipeline.outbox_path)

    def _write(self, verb: str, payload: dict, asset_id: str | None,
               apply_central, patch_replica) -> dict:
        if central_is_up(self._central):
            return apply_central()
        conn = self._outbox()
        enqueue(conn, verb, payload, asset_id=asset_id)
        conn.close()
        rep = _replica.open_replica(self._pipeline.local_cache)
        result = patch_replica(rep)
        rep.commit()
        rep.close()
        return result

    def declare(self, asset_type: str, actor: str) -> dict:
        asset_id = str(uuid.uuid4())                      # durable id, offline or not (spec §5.3)
        payload = {"id": asset_id, "asset_type": asset_type, "created_by": actor}
        asset = {"id": asset_id, "name": asset_id, "asset_type": asset_type,
                 "status": "declared", "created_by": actor, "updated_at": None}
        def patch(rep):
            _replica.upsert_asset(rep, asset)
            return asset
        def apply_central():
            result = self._central.declare(asset_type, actor, asset_id=asset_id)
            if isinstance(result, str):
                return {"id": result, "asset_type": asset_type}
            return result
        return self._write("declare", payload, asset_id, apply_central, patch)

    def bind_source(self, asset_id: str, location_uri: str, tool: str,
                    revision: str, published_by: str) -> dict:
        payload = {"asset_id": asset_id, "location_uri": location_uri,
                   "tool": tool, "revision": revision, "published_by": published_by}
        def patch(rep):
            asset = _replica.get_asset(rep, asset_id) or {"id": asset_id, "name": asset_id,
                                                          "asset_type": "unknown"}
            asset["source"] = {"location_uri": location_uri, "tool": tool, "revision": revision}
            _replica.upsert_asset(rep, asset)
            return asset
        return self._write("bind_source", payload, asset_id,
                           lambda: self._central.bind_source(asset_id, location_uri, tool, revision, published_by),
                           patch)

    def relate(self, from_asset: str, to_asset: str, rel_type: str,
               binding_mode: str = "float", actor: str | None = None) -> dict:
        payload = {"from_asset": from_asset, "to_asset": to_asset, "rel_type": rel_type,
                   "binding_mode": binding_mode, "actor": actor}
        def patch(rep):
            _replica.upsert_relation(rep, from_asset, to_asset, rel_type, binding_mode)
            return payload
        return self._write("relate", payload, from_asset,
                           lambda: self._central.relate(from_asset, to_asset, rel_type,
                                                         binding_mode=binding_mode, actor=actor),
                           patch)

    def rename(self, asset_id: str, new_name: str, actor: str) -> dict:
        payload = {"asset_id": asset_id, "new_name": new_name, "actor": actor}
        def patch(rep):
            asset = _replica.get_asset(rep, asset_id)
            if asset:
                asset["name"] = new_name
                _replica.upsert_asset(rep, asset)
            return payload
        return self._write("rename", payload, asset_id,
                           lambda: self._central.rename(asset_id, new_name, actor), patch)

    def relocate(self, asset_id: str, new_location_uri: str, actor: str,
                 facet: str = "source", new_revision: str | None = None) -> dict:
        payload = {"asset_id": asset_id, "new_location_uri": new_location_uri, "actor": actor,
                   "facet": facet, "new_revision": new_revision}
        def patch(rep):
            asset = _replica.get_asset(rep, asset_id)
            if asset:
                asset.setdefault("source", {})["location_uri"] = new_location_uri
                _replica.upsert_asset(rep, asset)
            return payload
        return self._write("relocate", payload, asset_id,
                           lambda: self._central.relocate(asset_id, new_location_uri, actor,
                                                          facet=facet, new_revision=new_revision),
                           patch)

    def bulk_relocate(self, moves: list[dict]) -> dict:
        payload = {"moves": moves}
        def patch(rep):
            for m in moves:
                asset = _replica.get_asset(rep, m["asset_id"])
                if asset:
                    asset.setdefault("source", {})["location_uri"] = m["new_location_uri"]
                    _replica.upsert_asset(rep, asset)
            return payload
        return self._write("bulk_relocate", payload, None,
                           lambda: self._central.bulk_relocate(moves), patch)

    # ---- health ----

    def health(self) -> dict:
        local = "down"
        try:
            body = self._local_get("/health")
            if body.get("app") == "assetcore-local-reader":
                local = "up"
        except Exception:
            pass
        conn = self._outbox()
        pending, failed = pending_count(conn), failed_count(conn)
        conn.close()
        return {"central": "up" if central_is_up(self._central) else "down",
                "local_reader": local,
                "outbox_pending": pending, "outbox_failed": failed}
