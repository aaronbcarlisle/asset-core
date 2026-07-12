"""PostgresBroadcastSink against a real Postgres — gated on ASSETCORE_TEST_DSN
(runs in the CI postgres job, skips locally).

Proves the durable multi-process spine: emit lands in the event table with a
BIGSERIAL seq, live NOTIFYs fan out to subscribers, catch-up replays by seq, and
— the whole point — the seq/history survive a "restart" (a fresh sink instance
over the same database), which the in-process BroadcastSink cannot offer.
"""
import asyncio
import os

import pytest

DSN = os.environ.get("ASSETCORE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ASSETCORE_TEST_DSN not set")
pytest.importorskip("psycopg2")


@pytest.fixture
def clean_db():
    from assetcore.infra.postgres_repo import PostgresRepo
    repo = PostgresRepo(DSN)
    repo.reset()
    yield repo
    repo.close()


def _event(event_type="declared", actor="amy"):
    from assetcore.core.entities import Event
    return Event(None, event_type, {"k": "v"}, actor)


def test_emit_then_history_replays_normalized_dicts(clean_db):
    from assetcore.infra.postgres_broadcast_sink import PostgresBroadcastSink
    sink = PostgresBroadcastSink(DSN)
    try:
        sink.emit(_event("declared"))
        sink.emit(_event("source.published"))
        entries = sink.history(0)
        assert [e["event_type"] for _s, e in entries] == ["declared", "source.published"]
        seqs = [s for s, _e in entries]
        assert seqs == sorted(seqs) and len(seqs) == 2
        # normalized shape the SSE layer expects
        assert {"event_id", "asset_id", "event_type", "payload", "actor",
                "occurred_at"} <= set(entries[0][1])
        assert sink.history(seqs[0]) == entries[1:]     # catch-up by seq
        assert sink.has_gap(0) is False                  # durable log never gaps
        assert sink.last_seq == seqs[-1]
    finally:
        sink.close()


def test_live_fanout_to_multiple_subscribers(clean_db):
    from assetcore.infra.postgres_broadcast_sink import PostgresBroadcastSink
    sink = PostgresBroadcastSink(DSN)

    async def drive():
        q1, q2 = sink.subscribe(), sink.subscribe()
        await asyncio.sleep(1.5)                 # let the LISTEN connection settle
        sink.emit(_event("identity.claimed", actor="pat"))
        e1 = await asyncio.wait_for(q1.get(), timeout=10)
        e2 = await asyncio.wait_for(q2.get(), timeout=10)
        return e1, e2

    try:
        (s1, d1), (s2, d2) = asyncio.run(drive())
        assert s1 == s2 and d1["event_type"] == "identity.claimed" == d2["event_type"]
        assert d1["actor"] == "pat"
        assert d1["event_id"]                    # live delivery carries the dedupe id
    finally:
        sink.close()


def test_seq_survives_restart_and_resume_replays_the_gap(clean_db):
    from assetcore.infra.postgres_broadcast_sink import PostgresBroadcastSink
    first = PostgresBroadcastSink(DSN)
    first.emit(_event("declared"))
    first.emit(_event("source.published"))
    cursor = first.history(0)[0][0]              # client saw only the first event
    first.close()

    # a "restarted" service: new sink instance, same database — the in-memory
    # BroadcastSink would reset seq to 0 here; the durable spine must not.
    second = PostgresBroadcastSink(DSN)
    try:
        second.emit(_event("identity.claimed"))
        replay = second.history(cursor)
        assert [e["event_type"] for _s, e in replay] == ["source.published", "identity.claimed"]
        assert second.last_seq > cursor
    finally:
        second.close()


def test_event_source_catchup_then_follow_over_postgres(clean_db):
    from assetcore.infra.postgres_broadcast_sink import PostgresBroadcastSink
    from assetcore.service.events import event_source
    sink = PostgresBroadcastSink(DSN)
    sink.emit(_event("declared"))

    class _FakeRequest:
        async def is_disconnected(self):
            return False

    async def drive():
        gen = event_source(sink, _FakeRequest(), after_seq=0)
        frames = [await gen.__anext__()]                 # catch-up from the table
        await asyncio.sleep(1.5)                         # listener settles
        sink.emit(_event("runtime.cooked"))
        frames.append(await asyncio.wait_for(gen.__anext__(), timeout=10))  # live
        await gen.aclose()
        return frames

    try:
        frames = asyncio.run(drive())
        assert "event: declared" in frames[0]
        assert "event: runtime.cooked" in frames[1]
    finally:
        sink.close()
