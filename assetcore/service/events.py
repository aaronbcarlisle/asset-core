"""SSE fan-out for the event spine.

Turns the BroadcastSink into a Server-Sent Events stream. On connect a subscriber
optionally passes ?after_seq=N and is first replayed everything it missed from the
durable log, then streamed live events — the catch-up-then-follow pattern from
ARCHITECTURE Part 7.2 (the log is durable; the live push is the low-latency hint).
"""
import asyncio
import json

from assetcore.core.entities import Event
from assetcore.infra.broadcast_sink import BroadcastSink

_KEEPALIVE_SECONDS = 15


def _format(seq: int, event: Event) -> str:
    data = {
        "seq": seq,
        "event_id": str(event.id),            # stable id for subscriber-side dedupe
        "asset_id": str(event.asset_id) if event.asset_id is not None else None,
        "event_type": event.event_type,
        "payload": event.payload,
        "actor": event.actor,
        "occurred_at": event.occurred_at.isoformat(),
    }
    # SSE frame: the seq is the SSE id (the reconnect cursor / Last-Event-ID).
    return f"id: {seq}\nevent: {event.event_type}\ndata: {json.dumps(data)}\n\n"


def _gap_frame(after_seq: int, dropped_seq: int) -> str:
    """A synthetic SSE frame telling a resuming client it fell behind the in-memory
    log horizon: events between its cursor and what we still retain were evicted, so
    it should re-sync from full state rather than trust an incremental follow."""
    import json
    data = {"event_type": "stream.gap", "after_seq": after_seq, "dropped_through": dropped_seq,
            "detail": "requested cursor is behind the retained event log; re-sync from full state"}
    return f"event: gap\ndata: {json.dumps(data)}\n\n"


async def event_source(sink: BroadcastSink, request, after_seq: int = 0):
    """Async generator yielding SSE frames: catch-up replay, then live follow.

    If the requested `after_seq` is behind the bounded log's horizon, a `gap` frame
    is sent first (the missed events are gone from memory), then whatever is still
    retained is replayed and live follow continues.
    """
    queue = sink.subscribe()                     # subscribe first, so nothing is missed
    try:
        if sink.has_gap(after_seq):
            yield _gap_frame(after_seq, sink.dropped_seq)
        replayed = sink.history(after_seq)
        last = replayed[-1][0] if replayed else after_seq
        for seq, event in replayed:
            yield _format(seq, event)
        while True:
            if await request.is_disconnected():
                return
            try:
                seq, event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"          # comment frame keeps the connection warm
                continue
            if seq <= last:
                continue                         # already sent during replay
            yield _format(seq, event)
    finally:
        sink.unsubscribe(queue)
