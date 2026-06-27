"""Event automation (EventRouter, SSE parse) + artist open-source helpers."""
import pytest

from assetcore.sdk import tools
from assetcore.sdk.automation import EventRouter, parse_sse


# --- EventRouter (pure dispatch) -------------------------------------------
def test_router_dispatch_by_type_and_wildcard():
    r = EventRouter()
    seen = []
    r.on("source.published", lambda e: seen.append(("pub", e["asset_id"])))

    @r.on("*")
    def _audit(e):
        seen.append(("*", e["event_type"]))

    n = r.dispatch({"event_type": "source.published", "asset_id": "x"})
    assert n == 2 and ("pub", "x") in seen and ("*", "source.published") in seen

    seen.clear()
    assert r.dispatch({"event_type": "declared", "asset_id": "y"}) == 1   # only wildcard


def test_router_handler_error_is_isolated():
    r = EventRouter()
    ran = []

    def boom(_e):
        raise RuntimeError("bad recipe")

    r.on("e", boom)
    r.on("e", lambda e: ran.append(1))
    r.dispatch({"event_type": "e"})            # boom must not stop the second handler
    assert ran == [1]


def test_router_run_respects_limit():
    r = EventRouter()
    got = []
    r.on("*", lambda e: got.append(e))
    assert r.run(iter([{"event_type": "x"}] * 5), limit=3) == 3 and len(got) == 3


def test_parse_sse_reads_data_frames():
    lines = ["id: 1", "event: declared",
             'data: {"seq": 1, "event_type": "declared", "asset_id": "a"}', "",
             ": keep-alive", "data: not-json"]
    evs = list(parse_sse(lines))
    assert len(evs) == 1 and evs[0]["event_type"] == "declared" and evs[0]["seq"] == 1


# --- artist open-source -----------------------------------------------------
class _FakeRegistry:
    def fetch(self, uri):
        return "/local/" + uri.lstrip("/")


def test_open_source_resolves_and_fetches(make_client):
    c = make_client("artist-token")
    aid = c.declare("prop", "amy")
    c.bind_source(aid, "//depot/art/x.ma", "maya", "1", "amy")
    assert tools.source_location(c, aid) == "//depot/art/x.ma"
    assert tools.fetch_source(c, aid, _FakeRegistry()) == "/local/depot/art/x.ma"


def test_open_source_without_source_raises(make_client):
    c = make_client("artist-token")
    aid = c.declare("prop", "amy")                 # no source facet yet
    assert tools.source_location(c, aid) is None
    with pytest.raises(ValueError):
        tools.fetch_source(c, aid, _FakeRegistry())
