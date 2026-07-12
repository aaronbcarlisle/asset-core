"""NotifySink — a Postgres LISTEN/NOTIFY EventSink (production event spine).

The swap-in for BroadcastSink when Postgres is the live target (deferred here
since Phase 3). emit() appends to the durable `event` table AND issues a NOTIFY;
the table is the source of truth (catch-up replays from it), NOTIFY is the
low-latency hint (ARCHITECTURE Part 5 / 7.2). Same EventSink port as the in-process
sink — the *emit* side swaps with no change above.

Scope note: this implements the EventSink port (emit) + a durable history(), not
the BroadcastSink subscribe/unsubscribe streaming API. So a NotifySink alone does
NOT power the /events SSE endpoint — that needs a small LISTEN->queue bridge
process; the /events route degrades cleanly (501) if given a non-subscribable
sink. Until that bridge lands, run BroadcastSink for live SSE and NotifySink for
the durable cross-process log.

Exercised only with a live Postgres (psycopg2 imported lazily); covered by the
gated tests/integration/test_postgres_notify.py when ASSETCORE_TEST_DSN is set.
"""
from __future__ import annotations

import json
import logging

from assetcore.core.entities import Event

CHANNEL = "assetcore_events"

logger = logging.getLogger(__name__)

# Postgres caps a NOTIFY payload at 8000 bytes; stay under it with margin. The
# durable `event` table row is always written (the source of truth) — only the
# low-latency NOTIFY hint is skipped when a payload is too large, and subscribers
# still catch up from the table by seq.
_NOTIFY_MAX_BYTES = 7500


def _notify_payload_fits(payload_json: str, limit: int = _NOTIFY_MAX_BYTES) -> bool:
    """True when the JSON NOTIFY payload is within Postgres's per-message limit."""
    return len(payload_json.encode("utf-8")) <= limit


class NotifySink:
    """Satisfies core.ports.EventSink, backed by the Postgres event table + NOTIFY."""

    def __init__(self, dsn: str) -> None:
        import psycopg2
        import psycopg2.extras
        psycopg2.extras.register_uuid()      # so a UUID asset_id adapts in the INSERT
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
            # and catch up from the event table by seq on (re)connect. Oversized
            # payloads (Postgres caps NOTIFY at 8000 bytes) would raise and abort the
            # whole emit — so skip just the hint when too big; the durable row above
            # is already committed and subscribers still catch up from the table.
            notify_json = json.dumps(payload)
            if _notify_payload_fits(notify_json):
                cur.execute(f"NOTIFY {CHANNEL}, %s", (notify_json,))
            else:
                logger.warning(
                    "event %s payload too large for NOTIFY (%d bytes); wrote durable row, "
                    "skipped live hint — subscribers catch up from the event table by seq",
                    payload["event_id"], len(notify_json.encode("utf-8")))

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
