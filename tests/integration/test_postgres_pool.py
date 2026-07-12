"""Pooled PostgresRepo + the threadpool-offload seam — gated on ASSETCORE_TEST_DSN.

Proves D3: repo calls check connections out of a pool (safe from worker threads,
no single-connection serialization), pool exhaustion degrades to bounded waiting
rather than instant errors, and create_app flips offload_db on exactly when both
the repo and the sink declare SUPPORTS_CONCURRENCY.
"""
import os
import threading

import pytest

DSN = os.environ.get("ASSETCORE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ASSETCORE_TEST_DSN not set")
pytest.importorskip("psycopg2")
pytest.importorskip("fastapi")


def _repo(**kw):
    from assetcore.infra.postgres_repo import PostgresRepo
    repo = PostgresRepo(DSN, **kw)
    repo.reset()
    return repo


def test_parallel_reads_do_not_serialize_or_race():
    from assetcore.app import verbs
    from assetcore.infra.inmemory_repo import InMemorySink
    repo = _repo()
    sink = InMemorySink()
    ids = [verbs.declare(repo, sink, "prop", "amy") for _ in range(10)]

    errors, results = [], []
    barrier = threading.Barrier(8)

    def read():
        try:
            barrier.wait()
            for aid in ids:
                assert repo.get_asset(aid) is not None
            results.append(len(repo.list_assets()))
        except Exception as exc:                    # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and results == [10] * 8
    repo.close()


def test_pool_exhaustion_waits_instead_of_erroring():
    # max_conn=1: the second concurrent operation must queue for the connection,
    # not blow up with PoolError.
    from assetcore.app import verbs
    from assetcore.infra.inmemory_repo import InMemorySink
    repo = _repo(min_conn=1, max_conn=1)
    sink = InMemorySink()
    errors = []
    barrier = threading.Barrier(4)

    def declare():
        try:
            barrier.wait()
            verbs.declare(repo, sink, "prop", "amy")
        except Exception as exc:                    # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=declare) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(repo.list_assets()) == 4
    repo.close()


def test_offload_flag_and_requests_work_in_offload_mode():
    from fastapi.testclient import TestClient
    from assetcore.infra.postgres_broadcast_sink import PostgresBroadcastSink
    from assetcore.service.app import create_app

    repo = _repo()
    sink = PostgresBroadcastSink(DSN)
    app = create_app(repo=repo, sink=sink)
    assert app.state.offload_db is True             # both ends thread-safe

    tc = TestClient(app)
    aid = tc.post("/assets", json={"asset_type": "prop", "created_by": "amy"},
                  headers={"X-Assetcore-Token": "artist-token"}).json()["id"]
    r = tc.post(f"/assets/{aid}/source",
                json={"location_uri": "//d/a.ma", "tool": "maya", "revision": "1",
                      "published_by": "amy"}, headers={"X-Assetcore-Token": "artist-token"})
    assert r.status_code == 200
    assert tc.get(f"/assets/{aid}").json()["source"]["location_uri"] == "//d/a.ma"
    assert tc.get("/metrics").json()["assets_total"] == 1
    sink.close()
    repo.close()


def test_offload_stays_off_for_mixed_combos():
    # pg repo + in-process BroadcastSink: the sink is loop-confined, so DB work
    # must stay inline — offloading would emit onto an asyncio.Queue from a
    # foreign thread.
    from assetcore.infra.broadcast_sink import BroadcastSink
    from assetcore.service.app import create_app
    repo = _repo()
    app = create_app(repo=repo, sink=BroadcastSink())
    assert app.state.offload_db is False
    repo.close()
