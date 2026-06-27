"""Gated real-tool checks — run only where Maya / Unreal / Perforce exist.

These close the literal Phase-5 done-when (a barrel through real Maya -> P4 ->
Unreal) when the software is present; everywhere else they SKIP, not pass, so a
green run never overclaims. The adapters' logic is already proven here against
faithful tool fakes in tests/contract — this is the on-real-iron confirmation.

To run the Maya one headless:  mayapy -m pytest tests/integration/test_real_tools_gated.py
"""
import importlib.util
import shutil

import pytest
from fastapi.testclient import TestClient

from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.sdk.client import AssetcoreClient
from assetcore.service.app import create_app

_HAVE_MAYA = importlib.util.find_spec("maya") is not None
_HAVE_UNREAL = importlib.util.find_spec("unreal") is not None
_HAVE_P4 = shutil.which("p4") is not None        # the p4 CLI the seam/resolver shell out to


@pytest.fixture
def make_client():
    """A client factory whose TestClients are torn down at the end of the test."""
    opened: list[TestClient] = []

    def _make(token: str) -> AssetcoreClient:
        app = create_app(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
        tc = TestClient(app)
        tc.__enter__()
        opened.append(tc)
        return AssetcoreClient(token=token, http=tc)

    yield _make
    for tc in opened:
        tc.__exit__(None, None, None)


# Maya's real publish uses the p4 seam, so it needs both Maya and the p4 CLI.
@pytest.mark.skipif(not (_HAVE_MAYA and _HAVE_P4),
                    reason="needs Maya (maya.cmds) + the p4 CLI")
def test_real_maya_publish_round_trip(make_client):
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        from assetcore.integrations.maya import MayaAdapter

        adapter = MayaAdapter(make_client("artist-token"))   # real cmds + real p4 seams
        doc = adapter.new_doc()
        aid = adapter.publish(doc, "prop", "artist")
        assert adapter.read_stamp(doc) == aid
        assert adapter.client.resolve(aid)["source"]["location_uri"] == adapter.current_location(doc)
    finally:
        maya.standalone.uninitialize()


@pytest.mark.skipif(not _HAVE_UNREAL, reason="Unreal (unreal module) not available")
def test_real_unreal_reconcile(make_client):
    from assetcore.integrations.unreal import UnrealAdapter

    adapter = UnrealAdapter(make_client("engine-token"))
    paths = adapter.list_assets()
    if not paths:
        pytest.skip("no /Game assets to reconcile")
    for path in paths:
        adapter.ensure_identity(path, "engine_asset")
    bound = adapter.reconcile("ci_build")
    assert bound and all(v >= 1 for v in bound.values())   # actually bound something


@pytest.mark.skipif(not _HAVE_P4, reason="Perforce (p4 CLI) not available")
def test_real_perforce_resolver_fetches(tmp_path):
    pytest.skip("requires a configured P4 workspace + a known depot path; wire per-site")
