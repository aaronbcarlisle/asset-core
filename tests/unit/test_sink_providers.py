"""Sink provider selection + the SSE layer's dict-entry normalization.

The durable PostgresBroadcastSink yields (seq, dict) entries where the in-process
BroadcastSink yields (seq, Event); event_source must serve both identically. The
Postgres sink itself is exercised by the gated tests in
tests/integration/test_postgres_spine.py — here we prove the seam with a fake.
"""
import asyncio

import assetcore.infra._providers  # noqa: F401 — registration side-effects
from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.sdk import providers
from assetcore.service.events import event_source


def test_sink_providers_registered():
    assert "broadcast" in providers.available("sink")
    assert "postgres" in providers.available("sink")
    assert providers.required_keys("sink", "postgres") == ("dsn",)


def test_broadcast_sink_builds_with_max_log():
    sink = providers.build("sink", "broadcast", {"max_log": "42"})
    assert isinstance(sink, BroadcastSink)
    for _ in range(50):
        sink.emit(_event())
    assert len(sink.events) == 42          # the config took effect


def _event():
    from assetcore.core.entities import Event
    return Event(None, "declared", actor="amy")


class DictEntrySink:
    """The PostgresBroadcastSink surface, with normalized-dict entries."""

    def __init__(self):
        self._entries = []
        self._queues = []

    def emit_dict(self, seq, event_type):
        entry = (seq, {"event_id": None, "asset_id": None, "event_type": event_type,
                       "payload": {}, "actor": "amy", "occurred_at": "2026-07-12T00:00:00+00:00"})
        self._entries.append(entry)
        for q in self._queues:
            q.put_nowait(entry)

    def history(self, after_seq=0):
        return [(s, e) for s, e in self._entries if s > after_seq]

    def has_gap(self, after_seq):
        return False

    dropped_seq = 0

    @property
    def last_seq(self):
        return self._entries[-1][0] if self._entries else 0

    def subscribe(self):
        q = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q):
        self._queues.remove(q)


def test_default_app_keeps_db_work_inline():
    # sqlite + BroadcastSink are loop-confined: offload must stay off, and
    # requests must behave exactly as before.
    import pytest
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from assetcore.infra.sqlite_repo import SqliteRepo
    from assetcore.service.app import create_app

    app = create_app(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
    assert app.state.offload_db is False
    tc = TestClient(app)
    aid = tc.post("/assets", json={"asset_type": "prop", "created_by": "amy"},
                  headers={"X-Assetcore-Token": "artist-token"}).json()["id"]
    assert tc.get(f"/assets/{aid}").status_code == 200


def test_event_source_serves_dict_entries():
    sink = DictEntrySink()
    sink.emit_dict(1, "declared")
    sink.emit_dict(2, "source.published")

    class _FakeRequest:
        async def is_disconnected(self):
            return False

    async def drive():
        gen = event_source(sink, _FakeRequest(), after_seq=0)
        frames = [await gen.__anext__(), await gen.__anext__()]   # replay
        sink.emit_dict(3, "identity.claimed")                     # live follow
        frames.append(await gen.__anext__())
        await gen.aclose()
        return frames

    frames = asyncio.run(drive())
    assert frames[0].startswith("id: 1\nevent: declared\n")
    assert frames[1].startswith("id: 2\nevent: source.published\n")
    assert frames[2].startswith("id: 3\nevent: identity.claimed\n")
    assert '"seq": 3' in frames[2] and '"actor": "amy"' in frames[2]
