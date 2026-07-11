from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import TypedDict


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
    """Read central state; True if it already reflects this entry (spec §5.3, §7.5.4)."""
    p, verb = entry["payload"], entry["verb"]
    try:
        if verb == "declare":
            aid = p.get("id")
            return bool(aid and client.resolve(aid) is not None)
        if verb == "bind_source":
            cur = client.get_source(p["asset_id"])
            return bool(cur and cur["location_uri"] == p["location_uri"]
                        and str(cur["revision"]) >= str(p["revision"]))
        if verb == "relocate":
            cur = client.get_source(p["asset_id"])
            return bool(cur and cur["location_uri"] == p["new_location_uri"])
        if verb == "relate":
            return False
        return False
    except Exception:
        return False


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
            mark_failed(conn, entry["id"], repr(exc))
            failed += 1
            if aid:
                failed_assets.add(aid)
    return {"replayed": replayed, "failed": failed, "skipped": skipped, "held": held}
