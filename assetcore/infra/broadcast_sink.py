"""BroadcastSink — an in-process EventSink that fans out to live subscribers.

Satisfies core.ports.EventSink. Every emit() is (a) appended to a durable,
sequence-numbered log so a reconnecting subscriber can catch up on what it
missed, and (b) pushed onto each live subscriber's queue for low-latency SSE
delivery. The log is the source of truth; the live push is just the fast hint —
the same shape as the Postgres event-table + LISTEN/NOTIFY design in ARCHITECTURE
Part 5/7.2, which a notify_sink will swap in (same port) when Postgres is live.

All access happens on the service's single event loop, so the plain deque / set /
asyncio.Queue here need no extra locking.

Scope / durability limits (documented, not hidden — see README + OPERATIONS):
  * The log is IN-MEMORY and bounded (`max_log`). Beyond that horizon the oldest
    entries are dropped; a subscriber resuming from before the horizon is told with
    a `gap` (via `history_or_gap`) so it re-syncs from full state instead of
    silently missing events.
  * `seq` resets to 0 on process restart. Cross-restart durable resume needs the
    Postgres NotifySink (durable `event` table). This sink is single-process:
    multiple uvicorn workers each get their own sink (and their own in-memory log).
"""
import asyncio
from collections import deque

from assetcore.core.entities import Event

_DEFAULT_MAX_LOG = 10_000


class BroadcastSink:
    """Satisfies core.ports.EventSink, plus subscribe/history for the SSE layer."""

    def __init__(self, max_log: int = _DEFAULT_MAX_LOG) -> None:
        if max_log < 1:
            raise ValueError("max_log must be >= 1")
        self._max_log = max_log
        self._log: deque[tuple[int, Event]] = deque(maxlen=max_log)
        self._seq = 0
        self._dropped_seq = 0            # highest seq evicted from the bounded log
        self._subscribers: set[asyncio.Queue] = set()

    # --- EventSink port ---
    def emit(self, event: Event) -> None:
        self._seq += 1
        entry = (self._seq, event)
        # deque(maxlen) evicts the oldest on overflow; record what we dropped so a
        # resume from before the horizon can be detected instead of silently gapped.
        if len(self._log) == self._max_log and self._log:
            self._dropped_seq = self._log[0][0]
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

    def has_gap(self, after_seq: int) -> bool:
        """True when a resume from `after_seq` would miss events already evicted.

        i.e. the client wants events after `after_seq`, but the oldest we still
        retain is newer than the very next one it expects.
        """
        if after_seq <= 0 or not self._log:
            return False
        earliest = self._log[0][0]
        return after_seq + 1 < earliest

    @property
    def dropped_seq(self) -> int:
        return self._dropped_seq

    @property
    def last_seq(self) -> int:
        return self._seq

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
