"""PostgresBroadcastSink — the durable, multi-process event spine.

Satisfies core.ports.EventSink AND the subscribable surface the SSE layer needs
(subscribe/unsubscribe/history/has_gap/last_seq), so `/events` can run durably:

  * emit()      — INSERT into the `event` table (the source of truth; BIGSERIAL id
                  is the seq, which survives restarts) + a NOTIFY hint. Composes
                  NotifySink, so the payload shape and the 8KB size guard match.
  * history()   — replay from the table by seq. Complete — a durable log never
                  drops entries, so has_gap() is always False.
  * subscribe() — a single dedicated LISTEN connection, drained by one daemon
                  thread, fans NOTIFY payloads out to per-subscriber asyncio
                  queues via call_soon_threadsafe (each queue is delivered on the
                  loop it subscribed from).

This is the config-selected production swap for the in-process BroadcastSink
(same duck-typed surface, chosen in assetcore.toml — see infra/_providers.py).
Entries yielded to the SSE layer are normalized DICTS (service.events._as_data
handles both shapes). Requires psycopg2 (the `postgres` extra) and a live server;
covered by the gated tests in tests/integration/test_postgres_spine.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import select
import threading

from assetcore.core.entities import Event
from assetcore.infra.notify_sink import CHANNEL, NotifySink

logger = logging.getLogger(__name__)

_POLL_SECONDS = 1.0     # listener wake-up cadence (also bounds close() latency)


class PostgresBroadcastSink:
    """Satisfies core.ports.EventSink + the SSE layer's subscribable surface."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._notify = NotifySink(dsn)          # emit + durable history live here
        self._lock = threading.Lock()
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._listener: threading.Thread | None = None
        self._stop = threading.Event()

    # --- EventSink port ------------------------------------------------------
    def emit(self, event: Event) -> None:
        self._notify.emit(event)

    # --- durable log ----------------------------------------------------------
    def history(self, after_seq: int = 0) -> list[tuple[int, dict]]:
        return [(seq, {"event_id": None, **row})     # table rows carry no Event.id
                for seq, row in self._notify.history(after_seq)]

    def has_gap(self, after_seq: int) -> bool:
        return False                               # the table never evicts entries

    @property
    def dropped_seq(self) -> int:
        return 0

    @property
    def last_seq(self) -> int:
        with self._notify.conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) FROM event")
            return int(cur.fetchone()[0])

    # --- live fan-out ----------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()          # deliveries land on this loop
        with self._lock:
            self._subscribers[q] = loop
        self._ensure_listener()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(q, None)

    def _ensure_listener(self) -> None:
        if self._listener is not None and self._listener.is_alive():
            return
        self._stop.clear()
        self._listener = threading.Thread(target=self._listen_loop,
                                          name="assetcore-pg-listen", daemon=True)
        self._listener.start()

    def _listen_loop(self) -> None:
        import psycopg2
        conn = psycopg2.connect(self._dsn)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {CHANNEL}")
            while not self._stop.is_set():
                if not select.select([conn], [], [], _POLL_SECONDS)[0]:
                    continue                       # timeout: re-check the stop flag
                conn.poll()
                while conn.notifies:
                    note = conn.notifies.pop(0)
                    try:
                        payload = json.loads(note.payload)
                        entry = (int(payload.pop("seq")), payload)
                    except (ValueError, KeyError, TypeError):
                        logger.warning("ignoring malformed NOTIFY payload")
                        continue
                    with self._lock:
                        targets = list(self._subscribers.items())
                    for q, loop in targets:
                        loop.call_soon_threadsafe(q.put_nowait, entry)
        except Exception:                          # noqa: BLE001 — log, don't kill silently
            logger.exception("event listener thread died; live SSE fan-out stopped "
                             "(durable history is unaffected)")
        finally:
            conn.close()

    def close(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.join(timeout=_POLL_SECONDS * 2)
        self._notify.conn.close()
