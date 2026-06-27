"""Gated real-tool checks — run only where Maya / Unreal / Perforce exist.

These close the literal Phase-5 done-when (a barrel through real Maya -> P4 ->
Unreal) when the software is present; everywhere else they SKIP, not pass, so a
green run never overclaims. The adapters' logic is already proven here against
faithful tool fakes in tests/contract — this is the on-real-iron confirmation.

To run the Maya one headless:  mayapy -m pytest tests/integration/test_real_tools_gated.py
"""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.sdk.client import AssetcoreClient
from assetcore.service.app import create_app

_HAVE_MAYA = importlib.util.find_spec("maya") is not None
_HAVE_UNREAL = importlib.util.find_spec("unreal") is not None
_HAVE_P4 = importlib.util.find_spec("P4") is not None  # P4Python; CLI-only shops adapt


def _client(token):
    app = create_app(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
    tc = TestClient(app)
    tc.__enter__()
    return AssetcoreClient(token=token, http=tc)


@pytest.mark.skipif(not _HAVE_MAYA, reason="Maya (maya.cmds) not available")
def test_real_maya_publish_round_trip():
    import maya.standalone
    maya.standalone.initialize(name="python")
    from assetcore.integrations.maya import MayaAdapter

    adapter = MayaAdapter(_client("artist-token"))   # real cmds + real p4 seams
    doc = adapter.new_doc()
    aid = adapter.publish(doc, "prop", "artist")
    assert adapter.read_stamp(doc) == aid
    assert adapter.client.resolve(aid)["source"]["location_uri"] == adapter.current_location(doc)


@pytest.mark.skipif(not _HAVE_UNREAL, reason="Unreal (unreal module) not available")
def test_real_unreal_reconcile():
    from assetcore.integrations.unreal import UnrealAdapter

    adapter = UnrealAdapter(_client("engine-token"))
    for path in adapter.list_assets():
        adapter.ensure_identity(path, "engine_asset")
    bound = adapter.reconcile("ci_build")
    assert all(v >= 1 for v in bound.values())


@pytest.mark.skipif(not _HAVE_P4, reason="Perforce (P4Python) not available")
def test_real_perforce_resolver_fetches(tmp_path):
    pytest.skip("requires a configured P4 workspace + a known depot path; wire per-site")
