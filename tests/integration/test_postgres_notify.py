"""NotifySink against a real Postgres — gated on ASSETCORE_TEST_DSN.

Skips when no server is configured (the default in CI). When a DSN is present it
verifies the production event spine end to end: emit -> durable row + LISTEN/NOTIFY,
with the serial seq as the catch-up cursor and Event.id as the live dedupe key.
This locks in the BIGSERIAL-vs-UUID fix the real DB surfaced.
"""
import json
import os

import pytest

DSN = os.environ.get("ASSETCORE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ASSETCORE_TEST_DSN not set")
pytest.importorskip("psycopg2")


def test_notify_sink_emits_durable_row_and_notification():
    import psycopg2

    from assetcore.core.entities import Event
    from assetcore.infra.notify_sink import CHANNEL, NotifySink
    from assetcore.infra.postgres_repo import PostgresRepo

    PostgresRepo(DSN).reset()                      # clean schema + tables

    listen = psycopg2.connect(DSN)
    listen.autocommit = True
    listen.cursor().execute(f"LISTEN {CHANNEL}")

    sink = NotifySink(DSN)
    event = Event(asset_id=None, event_type="source.published", payload={"version": 2}, actor="mo")
    sink.emit(event)

    history = sink.history(0)
    assert len(history) == 1 and history[0][1]["event_type"] == "source.published"

    listen.poll()
    assert len(listen.notifies) == 1
    payload = json.loads(listen.notifies[0].payload)
    assert payload["event_id"] == str(event.id)    # live dedupe key
    assert isinstance(payload["seq"], int)         # catch-up cursor (the BIGSERIAL)
    listen.close()
