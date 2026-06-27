"""
Scenario + invariant tests for assetcore.

These double as executable documentation: each test is one of the real
production pain points from docs/DESIGN.md, proven to resolve through the four
verbs (declare/bind/relate/resolve) with no special-casing.

Run:  pytest -v      (or: python -m pytest tests/)
"""
import pytest
from assetcore import api
from assetcore.db.connection import SqliteDB


@pytest.fixture
def db():
    d = SqliteDB()
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Scenario 1 — barrel reuse & lineage
# ---------------------------------------------------------------------------
def test_barrel_reuse_and_lineage(db):
    # artist summons a barrel mid-work; no production involvement
    barrel = api.declare(db, "prop", "artist:env_amy",
                         {"declared_while_on": "pirate_ship"})
    assert api.resolve(db, barrel)["meta"]["lifecycle"] == "provisional"
    api.bind_source(db, barrel, "//depot/art/props/barrel_a.ma", "maya", 4101, "env_amy")

    # ship composes the barrel
    ship = api.declare(db, "set", "artist:env_amy")
    api.relate(db, ship, barrel, "COMPOSED_OF", "env_amy")

    # production claims it later (async backfill) -> now active
    api.claim(db, barrel, "Barrel, Weathered Oak", "props/containers/barrel", "prod:pat")
    assert api.resolve(db, barrel)["meta"]["lifecycle"] == "active"
    assert api.resolve(db, barrel)["identity"]["display_name"] == "Barrel, Weathered Oak"

    # castle REUSES the same barrel — no duplicate identity
    castle = api.declare(db, "set", "artist:env_ben")
    api.relate(db, castle, barrel, "COMPOSED_OF", "env_ben")

    # mossy variant: new identity, DERIVED_FROM the original
    mossy = api.declare(db, "prop", "artist:env_ben")
    api.relate(db, mossy, barrel, "DERIVED_FROM", "env_ben")

    used = {u["rel_type"] for u in api.used_by(db, barrel)}
    assert "COMPOSED_OF" in used and "DERIVED_FROM" in used
    # exactly two sets compose the SAME barrel — proving reuse, not copy
    composers = [u for u in api.used_by(db, barrel) if u["rel_type"] == "COMPOSED_OF"]
    assert len(composers) == 2

    # lineage of mossy points back to the original barrel
    lin = api.lineage(db, mossy)
    assert lin[0]["to_asset"] == barrel and lin[0]["rel_type"] == "DERIVED_FROM"


# ---------------------------------------------------------------------------
# Scenario 2 — Robin's locomotion from Batman's
# ---------------------------------------------------------------------------
def test_robin_locomotion_semantics(db):
    bat_set = api.declare(db, "locomotion_set", "anim_kay")
    bat_walk = api.declare(db, "anim", "anim_kay")
    bat_grap = api.declare(db, "anim", "anim_kay")
    for a in (bat_walk, bat_grap):
        api.relate(db, bat_set, a, "COMPOSED_OF", "anim_kay")

    rob_set = api.declare(db, "locomotion_set", "anim_lee")
    rob_grap = api.declare(db, "anim", "anim_lee")
    rob_cape = api.declare(db, "anim", "anim_lee")

    # robin's set: walk is SHARED (composes batman's identity directly),
    # grapple is FORKED, cape is unique
    api.relate(db, rob_set, bat_walk, "COMPOSED_OF", "anim_lee")   # live shared
    api.relate(db, rob_set, rob_grap, "COMPOSED_OF", "anim_lee")
    api.relate(db, rob_set, rob_cape, "COMPOSED_OF", "anim_lee")
    api.relate(db, rob_grap, bat_grap, "DERIVED_FROM", "anim_lee")  # forked

    # fixing batman's walk impacts BOTH sets (they share the identity)
    affected = {u["from_asset"] for u in api.used_by(db, bat_walk)}
    assert bat_set in affected and rob_set in affected

    # robin's grapple has lineage to batman's; cape has none
    assert api.lineage(db, rob_grap)[0]["to_asset"] == bat_grap
    assert api.lineage(db, rob_cape) == []


# ---------------------------------------------------------------------------
# Scenario 3 — materials bottleneck (float vs pin)
# ---------------------------------------------------------------------------
def test_materials_float_then_pin(db):
    mat = api.declare(db, "material", "mat_mo")
    api.bind_source(db, mat, "//depot/mat/face.sbsar", "substance", 5000, "mat_mo")

    anim = api.declare(db, "anim", "anim_lee")
    # float during blocking
    api.relate(db, anim, mat, "DEPENDS_ON", "anim_lee", binding_mode="float")
    r = api.resolve_dependency(db, anim, mat)
    assert r["mode"] == "float" and r["resolved_source"]["version_num"] == 1

    # materials publishes v2 — floating consumer sees it, no rebuild chain
    api.bind_source(db, mat, "//depot/mat/face.sbsar", "substance", 5050, "mat_mo")
    r = api.resolve_dependency(db, anim, mat)
    assert r["resolved_source"]["version_num"] == 2

    # pin before delivery; later v3 is ignored
    api.relate(db, anim, mat, "DEPENDS_ON", "anim_lee",
               binding_mode="pin", pinned_source_version=2)
    api.bind_source(db, mat, "//depot/mat/face.sbsar", "substance", 5099, "mat_mo")
    r = api.resolve_dependency(db, anim, mat)
    assert r["mode"] == "pin" and r["resolved_source"]["version_num"] == 2


# ---------------------------------------------------------------------------
# Bonus — resolve a stamped UUID to all three facets (the re-import horror fix)
# ---------------------------------------------------------------------------
def test_resolve_all_three_facets(db):
    a = api.declare(db, "prop", "env_amy")
    api.bind_source(db, a, "//depot/props/barrel.ma", "maya", 4101, "env_amy")
    api.claim(db, a, "Barrel", "props/barrel", "pat")
    api.bind_runtime(db, a, "/Game/Junk/Bob/BP_Barrel_FINAL", "build_8821")

    got = api.resolve(db, a)
    assert got["identity"]["display_name"] == "Barrel"
    assert got["source"]["depot_path"].endswith("barrel.ma")
    assert got["runtime"]["engine_path"] == "/Game/Junk/Bob/BP_Barrel_FINAL"


# ---------------------------------------------------------------------------
# Invariant — a rename touches ONLY the identity facet
# ---------------------------------------------------------------------------
def test_rename_does_not_touch_source_or_runtime(db):
    a = api.declare(db, "prop", "env_amy")
    api.bind_source(db, a, "//depot/props/barrel.ma", "maya", 4101, "env_amy")
    api.bind_runtime(db, a, "/Game/Props/Barrel", "build_1")
    before = api.resolve(db, a)

    api.rename(db, a, "Totally Different Name", "pat", new_taxonomy="props/new/place")
    after = api.resolve(db, a)

    assert after["identity"]["display_name"] == "Totally Different Name"
    assert after["source"] == before["source"]      # source pointer untouched
    assert after["runtime"] == before["runtime"]    # runtime pointer untouched


# ---------------------------------------------------------------------------
# Invariant — only one 'latest' source version at a time
# ---------------------------------------------------------------------------
def test_single_latest_source_version(db):
    a = api.declare(db, "prop", "env_amy")
    api.bind_source(db, a, "//depot/v1.ma", "maya", 1, "amy")
    api.bind_source(db, a, "//depot/v2.ma", "maya", 2, "amy")
    api.bind_source(db, a, "//depot/v3.ma", "maya", 3, "amy")
    latest = db.fetchall(
        "SELECT version_num FROM facet_source_version WHERE asset_id=? AND is_latest", (a,))
    assert len(latest) == 1 and latest[0]["version_num"] == 3
