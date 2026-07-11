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
