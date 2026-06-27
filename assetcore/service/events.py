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
        "asset_id": str(event.asset_id) if event.asset_id is not None else None,
        "event_type": event.event_type,
        "payload": event.payload,
        "actor": event.actor,
        "occurred_at": event.occurred_at.isoformat(),
    }
    # SSE frame: an id (for Last-Event-ID reconnects) and a data line.
    return f"id: {seq}\nevent: {event.event_type}\ndata: {json.dumps(data)}\n\n"


async def event_source(sink: BroadcastSink, request, after_seq: int = 0):
    """Async generator yielding SSE frames: catch-up replay, then live follow."""
    queue = sink.subscribe()                     # subscribe first, so nothing is missed
    try:
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
