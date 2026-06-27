"""NotifySink — a Postgres LISTEN/NOTIFY EventSink (production event spine).

The swap-in for BroadcastSink when Postgres is the live target (deferred here
since Phase 3). emit() appends to the durable `event` table AND issues a NOTIFY;
the table is the source of truth (catch-up replays from it), NOTIFY is the
low-latency hint (ARCHITECTURE Part 5 / 7.2). Same EventSink port as the in-process
sink — the service swaps one for the other with no change above.

Code-complete but exercised only with a live Postgres (psycopg2 is imported
lazily); no server is available in this environment, so it is not run here.
"""
from __future__ import annotations

import json

from assetcore.core.entities import Event

CHANNEL = "assetcore_events"


class NotifySink:
    """Satisfies core.ports.EventSink, backed by the Postgres event table + NOTIFY."""

    def __init__(self, dsn: str) -> None:
        import psycopg2
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def emit(self, event: Event) -> None:
        payload = {
            "event_id": str(event.id),
            "asset_id": str(event.asset_id) if event.asset_id is not None else None,
            "event_type": event.event_type,
            "payload": event.payload,
            "actor": event.actor,
            "occurred_at": event.occurred_at.isoformat(),
        }
        with self.conn.cursor() as cur:
            # The table's `id` is a BIGSERIAL sequence (the catch-up cursor) — let
            # it autogenerate. Event.id (a UUID, the live-delivery dedupe key) rides
            # in the NOTIFY envelope, not the serial PK.
            cur.execute(
                "INSERT INTO event (asset_id, event_type, payload, actor, occurred_at)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (event.asset_id, event.event_type, json.dumps(event.payload),
                 event.actor, event.occurred_at),
            )
            payload["seq"] = cur.fetchone()[0]
            # NOTIFY carries the low-latency hint; subscribers dedupe on event_id
            # and catch up from the event table by seq on (re)connect.
            cur.execute(f"NOTIFY {CHANNEL}, %s", (json.dumps(payload),))

    def history(self, after_seq: int = 0) -> list[tuple[int, dict]]:
        """Replay durable events with id (bigserial) > after_seq (reconnect catch-up)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, asset_id, event_type, payload, actor, occurred_at"
                " FROM event WHERE id > %s ORDER BY id", (after_seq,))
            return [(row[0], {
                "asset_id": str(row[1]) if row[1] else None, "event_type": row[2],
                "payload": row[3], "actor": row[4],
                "occurred_at": row[5].isoformat() if row[5] else None,
            }) for row in cur.fetchall()]
