"""The DCC contract — one suite every DCC adapter must pass (ARCHITECTURE Part 10).

Parameterized over two fakes with *different* stamping mechanisms (dict + sidecar
file). The definition of "a correct DCC integration" is executable: onboarding a
real tool later is making it pass this same suite, not hoping it behaves like Maya.
"""
from uuid import uuid4

import pytest

from assetcore.integrations.blender import BlenderAdapter
from assetcore.integrations.maya import MayaAdapter
from assetcore.integrations.substance import SubstanceAdapter
from assetcore.sdk.stamping import StampConflict
from tests.contract.fakes import (
    FakeBlenderScene,
    FakeBlenderVcs,
    FakeDCCAdapter,
    FakeMayaVcs,
    FakeMayaScene,
    FakeSidecarDCCAdapter,
    FakeSubstancePackage,
    FakeSubstanceVcs,
)

# (id, builder(make_client, tmp_path) -> adapter). The artist authority owns source.
# Every REAL DCC adapter runs the IDENTICAL suite via faithful fakes of its tool
# API. Maya/Blender/Substance are three different tools — same contract, no change
# below L4. That sameness IS the thesis (Phase 6, the swap test).
DCC_ADAPTERS = [
    pytest.param(lambda mk, tmp: FakeDCCAdapter(mk("artist-token")), id="dict-stamp"),
    pytest.param(lambda mk, tmp: FakeSidecarDCCAdapter(mk("artist-token"), tmp), id="sidecar-stamp"),
    pytest.param(lambda mk, tmp: MayaAdapter(mk("artist-token"), scene=FakeMayaScene(),
                                             vcs=FakeMayaVcs()), id="maya"),
    pytest.param(lambda mk, tmp: BlenderAdapter(mk("artist-token"), scene=FakeBlenderScene(),
                                                vcs=FakeBlenderVcs()), id="blender"),
    pytest.param(lambda mk, tmp: SubstanceAdapter(mk("artist-token"), package=FakeSubstancePackage(),
                                                  vcs=FakeSubstanceVcs()), id="substance"),
]


@pytest.fixture(params=DCC_ADAPTERS)
def adapter(request, make_client, tmp_path):
    return request.param(make_client, tmp_path)


def test_publish_mints_and_stamps(adapter):
    doc = adapter.new_doc()
    aid = adapter.publish(doc, "prop", "artist")
    assert adapter.read_stamp(doc) == aid                    # stamped into the doc
    assert adapter.client.resolve(aid)["source"] is not None  # source facet bound


def test_republish_keeps_identity(adapter):
    doc = adapter.new_doc()
    aid = adapter.publish(doc, "prop", "artist")
    aid2 = adapter.publish(doc, "prop", "artist")            # save again
    assert aid == aid2                                       # SAME identity
    assert adapter.client.resolve(aid)["source"]["version_num"] == 2  # ...new version


def test_stamp_never_overwritten(adapter):
    doc = adapter.new_doc()
    adapter.write_stamp(doc, str(uuid4()))
    with pytest.raises(StampConflict):
        adapter.write_stamp(doc, str(uuid4()))              # a different id is refused


def test_stamp_rewrite_with_same_id_is_idempotent(adapter):
    doc = adapter.new_doc()
    aid = str(uuid4())
    adapter.write_stamp(doc, aid)
    adapter.write_stamp(doc, aid)                            # same id: no conflict
    assert adapter.read_stamp(doc) == aid


def test_reference_creates_float_edge(adapter):
    consumer = adapter.new_doc()
    adapter.publish(consumer, "anim", "artist")
    dep = adapter.new_doc()
    dep_id = adapter.publish(dep, "material", "artist")

    resolved = adapter.reference(consumer, dep_id, "float")
    assert resolved["version_num"] == 1                     # float -> current latest

    adapter.publish(dep, "material", "artist")              # dependency publishes v2
    consumer_id = adapter.read_stamp(consumer)
    # floating consumer now resolves to v2 with no re-reference (the bottleneck fix)
    assert adapter.client.resolve_dependency(consumer_id, dep_id)["version_num"] == 2


def test_round_trip_resolves_to_source(adapter):
    doc = adapter.new_doc()
    aid = adapter.publish(doc, "prop", "artist")
    # read the stamp back off the doc, resolve it, land on the authored location
    stamp = adapter.read_stamp(doc)
    assert stamp == aid
    assert adapter.client.resolve(stamp)["source"]["location_uri"] == adapter.current_location(doc)
