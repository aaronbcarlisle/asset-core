"""The swap test, executed (ARCHITECTURE Part 6 / Phase 6).

A barrel authored in Blender, an animation in Maya, a floating material from
Substance — three wildly different tools, all emitting the same universal verbs
against ONE unchanged core. The float dependency dissolves the materials
bottleneck across tool boundaries. If this reads as unremarkable, that's the win.
"""
from assetcore.integrations.blender import BlenderAdapter
from assetcore.integrations.maya import MayaAdapter
from assetcore.integrations.substance import SubstanceAdapter
from tests.contract.fakes import (
    FakeBlenderScene,
    FakeBlenderVcs,
    FakeMayaScene,
    FakeMayaVcs,
    FakeSubstancePackage,
    FakeSubstanceVcs,
)


def test_three_tools_one_core(make_client):
    # three tools, three adapters, all talking to the SAME service/core
    blender = BlenderAdapter(make_client("artist-token"), FakeBlenderScene(), FakeBlenderVcs())
    substance = SubstanceAdapter(make_client("artist-token"), FakeSubstancePackage(), FakeSubstanceVcs())
    maya = MayaAdapter(make_client("artist-token"), FakeMayaScene(), FakeMayaVcs())

    # author in three different tools
    barrel_doc = blender.new_doc()
    barrel = blender.publish(barrel_doc, "prop", "ben")
    mat_doc = substance.new_doc()
    mat = substance.publish(mat_doc, "material", "mo")
    anim_doc = maya.new_doc()
    anim = maya.publish(anim_doc, "anim", "lee")

    # each landed its own SOURCE facet, tagged with its own tool
    assert blender.client.resolve(barrel)["source"]["tool"] == "blender"
    assert substance.client.resolve(mat)["source"]["tool"] == "substance"
    assert maya.client.resolve(anim)["source"]["tool"] == "maya"

    # the Maya animation floats on the Substance material
    resolved = maya.reference(anim_doc, mat, "float")
    assert resolved["version_num"] == 1
    # ...and composes the Blender barrel
    maya.client.relate(anim, barrel, "COMPOSED_OF")

    # Substance publishes v2 — the floating Maya consumer sees it, no rebuild chain
    substance.publish(mat_doc, "material", "mo")
    assert maya.client.resolve_dependency(anim, mat)["version_num"] == 2

    # the Blender barrel knows it's used by the Maya animation (cross-tool lineage)
    assert any(u["from_asset"] == anim for u in maya.client.used_by(barrel))


def test_blender_and_maya_reference_the_same_substance_material(make_client):
    """Two consumers in different tools share one material identity (reuse, not copy)."""
    substance = SubstanceAdapter(make_client("artist-token"), FakeSubstancePackage(), FakeSubstanceVcs())
    blender = BlenderAdapter(make_client("artist-token"), FakeBlenderScene(), FakeBlenderVcs())
    maya = MayaAdapter(make_client("artist-token"), FakeMayaScene(), FakeMayaVcs())

    mat = substance.publish(substance.new_doc(), "material", "mo")

    b_doc = blender.new_doc(); blender.publish(b_doc, "prop", "ben")
    m_doc = maya.new_doc(); maya.publish(m_doc, "prop", "amy")
    assert blender.reference(b_doc, mat, "float")["version_num"] == 1
    assert maya.reference(m_doc, mat, "float")["version_num"] == 1

    # both tools depend on the one material; no duplicate material identity minted
    assert substance.client.resolve(mat)["source"]["version_num"] == 1
    assert {u["from_asset"] for u in blender.client.used_by(mat)} == set()  # DEPENDS_ON isn't 'used_by'
