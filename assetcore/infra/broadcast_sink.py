"""BroadcastSink — an in-process EventSink that fans out to live subscribers.

Satisfies core.ports.EventSink. Every emit() is (a) appended to a durable,
sequence-numbered log so a reconnecting subscriber can catch up on what it
missed, and (b) pushed onto each live subscriber's queue for low-latency SSE
delivery. The log is the source of truth; the live push is just the fast hint —
the same shape as the Postgres event-table + LISTEN/NOTIFY design in ARCHITECTURE
Part 5/7.2, which a notify_sink will swap in (same port) when Postgres is live.

All access happens on the service's single event loop, so the plain list / set /
asyncio.Queue here need no extra locking.
"""
import asyncio

from assetcore.core.entities import Event


class BroadcastSink:
    """Satisfies core.ports.EventSink, plus subscribe/history for the SSE layer."""

    def __init__(self) -> None:
        self._log: list[tuple[int, Event]] = []
        self._seq = 0
        self._subscribers: set[asyncio.Queue] = set()

    # --- EventSink port ---
    def emit(self, event: Event) -> None:
        self._seq += 1
        entry = (self._seq, event)
        self._log.append(entry)
        for q in list(self._subscribers):
            q.put_nowait(entry)

    # --- convenience: drop-in compatible with InMemorySink for assertions ---
    @property
    def events(self) -> list[Event]:
        return [e for _, e in self._log]

    # --- subscription / catch-up, used by the SSE endpoint ---
    def history(self, after_seq: int = 0) -> list[tuple[int, Event]]:
        return [(s, e) for s, e in self._log if s > after_seq]

    @property
    def last_seq(self) -> int:
        return self._seq

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
