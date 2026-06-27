"""L2 service tests via FastAPI TestClient — the only door, exercised over HTTP.

Covers verb round-trips, authority enforcement (the Part-3 table), domain-error
mapping, and the live event spine (SSE catch-up replay). Uses a real SqliteRepo +
BroadcastSink behind create_app, so the full stack from HTTP down to storage runs.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from assetcore.core.entities import Event
from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.service.app import create_app
from assetcore.service.events import event_source

ARTIST = {"X-Assetcore-Token": "artist-token"}
PROD = {"X-Assetcore-Token": "prod-token"}
ENGINE = {"X-Assetcore-Token": "engine-token"}


@pytest.fixture
def client():
    app = create_app(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
    with TestClient(app) as c:
        yield c


def _declare(client, asset_type="prop", by="env_amy", headers=ARTIST):
    r = client.post("/assets", json={"asset_type": asset_type, "created_by": by}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- round trips ------------------------------------------------------------
def test_declare_resolve_round_trip(client):
    aid = _declare(client)
    r = client.get(f"/assets/{aid}")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["lifecycle"] == "provisional"
    assert body["meta"]["asset_type"] == "prop"
    assert body["identity"]["display_name"] is None
    assert body["source"] is None and body["runtime"] is None


def test_full_three_facet_resolve(client):
    aid = _declare(client)
    assert client.post(f"/assets/{aid}/source",
                       json={"location_uri": "//depot/props/barrel.ma", "tool": "maya",
                             "revision": "4101", "published_by": "env_amy"},
                       headers=ARTIST).json() == {"version": 1}
    assert client.post(f"/assets/{aid}/claim",
                       json={"display_name": "Barrel", "taxonomy": "props/barrel", "actor": "pat"},
                       headers=PROD).status_code == 204
    assert client.post(f"/assets/{aid}/runtime",
                       json={"location_uri": "/Game/Bob/BP_Barrel", "build_id": "b1"},
                       headers=ENGINE).json() == {"version": 1}

    body = client.get(f"/assets/{aid}").json()
    assert body["meta"]["lifecycle"] == "active"
    assert body["identity"]["display_name"] == "Barrel"
    assert body["source"]["location_uri"].endswith("barrel.ma")
    assert body["runtime"]["location_uri"] == "/Game/Bob/BP_Barrel"


def test_relate_used_by_and_lineage(client):
    barrel = _declare(client)
    ship = _declare(client, asset_type="set")
    mossy = _declare(client)
    assert client.post("/relate", json={"from_asset": ship, "to_asset": barrel,
                                         "rel_type": "COMPOSED_OF", "actor": "amy"},
                       headers=ARTIST).status_code == 204
    assert client.post("/relate", json={"from_asset": mossy, "to_asset": barrel,
                                         "rel_type": "DERIVED_FROM", "actor": "ben"},
                       headers=ARTIST).status_code == 204

    used = client.get(f"/assets/{barrel}/used_by").json()
    assert {u["rel_type"] for u in used} == {"COMPOSED_OF", "DERIVED_FROM"}
    lin = client.get(f"/assets/{mossy}/lineage").json()
    assert lin[0]["to_asset"] == barrel and lin[0]["rel_type"] == "DERIVED_FROM"


def test_materials_float_then_pin_over_http(client):
    mat = _declare(client, asset_type="material")
    client.post(f"/assets/{mat}/source", json={"location_uri": "//m.sbsar", "tool": "substance",
                                                "revision": "5000", "published_by": "mo"}, headers=ARTIST)
    anim = _declare(client, asset_type="anim")
    client.post("/relate", json={"from_asset": anim, "to_asset": mat, "rel_type": "DEPENDS_ON",
                                 "actor": "lee", "binding_mode": "float"}, headers=ARTIST)
    assert client.get("/dependency", params={"frm": anim, "to": mat}).json()["version_num"] == 1

    client.post(f"/assets/{mat}/source", json={"location_uri": "//m.sbsar", "tool": "substance",
                                               "revision": "5050", "published_by": "mo"}, headers=ARTIST)
    assert client.get("/dependency", params={"frm": anim, "to": mat}).json()["version_num"] == 2

    client.post("/set_binding", json={"from_asset": anim, "to_asset": mat,
                                      "binding_mode": "pin", "pinned_version": 2}, headers=ARTIST)
    client.post(f"/assets/{mat}/source", json={"location_uri": "//m.sbsar", "tool": "substance",
                                               "revision": "5099", "published_by": "mo"}, headers=ARTIST)
    assert client.get("/dependency", params={"frm": anim, "to": mat}).json()["version_num"] == 2


# --- authority enforcement --------------------------------------------------
def test_declare_requires_a_token(client):
    r = client.post("/assets", json={"asset_type": "prop", "created_by": "amy"})
    assert r.status_code == 401


def test_claim_requires_production(client):
    aid = _declare(client)
    forbidden = client.post(f"/assets/{aid}/claim",
                            json={"display_name": "X", "taxonomy": "t", "actor": "amy"},
                            headers=ARTIST)
    assert forbidden.status_code == 403
    allowed = client.post(f"/assets/{aid}/claim",
                          json={"display_name": "X", "taxonomy": "t", "actor": "pat"},
                          headers=PROD)
    assert allowed.status_code == 204


def test_bind_source_rejects_engine_authority(client):
    aid = _declare(client)
    r = client.post(f"/assets/{aid}/source",
                    json={"location_uri": "//x.ma", "tool": "maya", "revision": "1",
                          "published_by": "amy"}, headers=ENGINE)
    assert r.status_code == 403


# --- error mapping ----------------------------------------------------------
def test_resolve_unknown_is_404(client):
    r = client.get("/assets/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_duplicate_edge_is_400(client):
    a, b = _declare(client), _declare(client, asset_type="set")
    body = {"from_asset": b, "to_asset": a, "rel_type": "COMPOSED_OF", "actor": "amy"}
    assert client.post("/relate", json=body, headers=ARTIST).status_code == 204
    assert client.post("/relate", json=body, headers=ARTIST).status_code == 400


# --- observability (Phase 8) -----------------------------------------------
def test_metrics_endpoint(client):
    a = _declare(client)
    client.post(f"/assets/{a}/source", json={"location_uri": "//x.ma", "tool": "maya",
                "revision": "1", "published_by": "amy"}, headers=ARTIST)
    b = _declare(client)
    client.post(f"/assets/{b}/claim", json={"display_name": "N", "taxonomy": "t",
                "actor": "pat"}, headers=PROD)

    m = client.get("/metrics").json()
    assert m["assets_total"] == 2
    assert m["lifecycle"]["provisional"] == 1 and m["lifecycle"]["active"] == 1
    assert m["source_coverage_pct"] == 50.0          # only `a` has a source
    assert m["provisional_count"] == 1
    assert m["events_emitted"] >= 3                   # declared x2 + source + claim
    assert m["request_count"] >= 1 and m["avg_latency_ms"] >= 0.0


# --- human surfaces (Phase 7) ----------------------------------------------
def test_find_similar_endpoint(client):
    a = _declare(client)
    client.post(f"/assets/{a}/claim", json={"display_name": "Weathered Barrel",
                "taxonomy": "props/containers/barrel", "actor": "pat"}, headers=PROD)
    b = _declare(client)
    client.post(f"/assets/{b}/claim", json={"display_name": "Wooden Crate",
                "taxonomy": "props/containers/crate", "actor": "pat"}, headers=PROD)

    hits = client.get("/similar", params={"name": "barrel", "asset_type": "prop"}).json()
    ids = [h["id"] for h in hits]
    assert a in ids and b not in ids
    assert hits[0]["score"] >= 1


def test_provisional_worklist_endpoint(client):
    pending = _declare(client)
    claimed = _declare(client)
    client.post(f"/assets/{claimed}/claim", json={"display_name": "N", "taxonomy": "t",
                "actor": "pat"}, headers=PROD)

    work = client.get("/worklist/provisional").json()
    ids = [w["id"] for w in work]
    assert pending in ids and claimed not in ids


def test_floating_dependencies_endpoint(client):
    anim = _declare(client, asset_type="anim")
    mat = _declare(client, asset_type="material")
    client.post("/relate", json={"from_asset": anim, "to_asset": mat, "rel_type": "DEPENDS_ON",
                "binding_mode": "float"}, headers=ARTIST)
    floating = client.get(f"/assets/{anim}/floating-dependencies").json()
    assert [e["to_asset"] for e in floating] == [mat]


# --- the event spine --------------------------------------------------------
def _types(frames: list[str]) -> list[str]:
    # each SSE frame is "id: N\nevent: <type>\ndata: {...}\n\n"
    return [f.split("event: ", 1)[1].split("\n", 1)[0] for f in frames if f.startswith("id:")]


def test_event_source_replays_then_follows():
    """Drive the SSE generator directly: catch-up replay, then live follow.

    Exercised here instead of over TestClient's streaming (which deadlocks on the
    long-poll); the real HTTP SSE transport is covered by the live uvicorn demo.
    """
    sink = BroadcastSink()
    sink.emit(Event(None, "declared", actor="amy"))
    sink.emit(Event(None, "source.published", actor="amy"))

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def drive() -> list[str]:
        gen = event_source(sink, _FakeRequest(), after_seq=0)
        frames = [await gen.__anext__(), await gen.__anext__()]   # the two replayed
        sink.emit(Event(None, "identity.claimed", actor="pat"))    # now live
        frames.append(await gen.__anext__())                       # follows through
        await gen.aclose()
        return frames

    assert _types(asyncio.run(drive())) == ["declared", "source.published", "identity.claimed"]


def test_event_source_catch_up_skips_already_seen():
    """A subscriber reconnecting with after_seq only gets what it missed."""
    sink = BroadcastSink()
    sink.emit(Event(None, "declared"))
    sink.emit(Event(None, "source.published"))

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def drive() -> list[str]:
        gen = event_source(sink, _FakeRequest(), after_seq=1)   # already saw seq 1
        frame = await gen.__anext__()
        await gen.aclose()
        return [frame]

    assert _types(asyncio.run(drive())) == ["source.published"]
