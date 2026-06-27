"""sdk/automation.py — event-driven automation over the spine (L3).

This is where asset management becomes pipeline: subscribe to the durable event
spine and run reactive recipes — on `source.published` notify the tracker / trigger
a cook; on `identity.claimed` mirror to ShotGrid; on `relationship.added` update a
dependency dashboard. EventRouter is the dispatch (pure, testable); stream_events
tails the service's SSE `/events` endpoint (catch-up replay, then live follow).

Pure stdlib + httpx + the SDK boundary; imports nothing below it (SDK firewall
covers this module). Studios write handlers; the core never learns the recipe.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable, Iterator

import httpx

Event = dict
Handler = Callable[[Event], None]


class EventRouter:
    """Maps an event_type -> handlers. Register with `on`; feed events via `run`/
    `dispatch`. The "*" type matches every event (e.g. an audit logger)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event_type: str, fn: Handler | None = None):
        """Register a handler. Usable as a decorator: `@router.on("source.published")`."""
        def _register(f: Handler) -> Handler:
            self._handlers.setdefault(event_type, []).append(f)
            return f
        return _register(fn) if fn is not None else _register

    def dispatch(self, event: Event) -> int:
        """Invoke every handler for this event's type (+ the "*" handlers). Returns
        how many fired. A handler raising does not stop the others."""
        fns = self._handlers.get(event.get("event_type", ""), []) + self._handlers.get("*", [])
        for fn in fns:
            try:
                fn(event)
            except Exception as exc:   # noqa: BLE001 — one bad recipe must not sink the stream
                print(f"[automation] handler error on {event.get('event_type')}: {exc}")
        return len(fns)

    def run(self, events: Iterable[Event], limit: int | None = None) -> int:
        """Dispatch a stream of events (e.g. stream_events(...)). `limit` stops after
        N events (handy for tests / bounded runs). Returns events processed."""
        n = 0
        for ev in events:
            if limit is not None and n >= limit:   # check before dispatch so limit=0 means zero
                break
            self.dispatch(ev)
            n += 1
        return n


def parse_sse(lines: Iterable[str]) -> Iterator[Event]:
    """Yield event dicts from raw SSE lines, reading each frame's `data: <json>`."""
    for line in lines:
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue   # keep-alive comments / partial frames: skip


def stream_events(base_url: str, token: str, after_seq: int = 0) -> Iterator[Event]:
    """Tail the service SSE /events: replay everything after `after_seq`, then follow
    live. Each yielded event is a dict (seq, event_id, asset_id, event_type, payload,
    actor, occurred_at). Requires a subscribable sink (BroadcastSink) on the service.
    """
    headers = {"X-Assetcore-Token": token}
    with httpx.Client(base_url=base_url, timeout=None) as http:
        with http.stream("GET", "/events", params={"after_seq": after_seq},
                         headers=headers) as r:
            r.raise_for_status()
            yield from parse_sse(r.iter_lines())
