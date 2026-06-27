"""The three scenarios + invariants as backend-agnostic functions.

Each takes (repo, sink) — any AssetRepo + EventSink — and asserts the same
guarantees. tests/unit runs them against InMemoryRepo (no I/O); tests/integration
runs the identical bodies against sqlite (and postgres when available). That a
single set of assertions passes on every backend IS the proof that the port
abstraction holds — storage is swappable without touching meaning.
"""
import dataclasses

from assetcore.app import verbs
from assetcore.core.types import BindingMode, Lifecycle, RelType


def scenario_barrel_reuse_and_lineage(repo, sink):
    # artist summons a barrel mid-work; no production involvement
    barrel = verbs.declare(repo, sink, "prop", "artist:env_amy",
                           {"declared_while_on": "pirate_ship"})
    assert verbs.resolve(repo, barrel)["meta"].lifecycle == Lifecycle.PROVISIONAL
    verbs.bind_source(repo, sink, barrel, "//depot/art/props/barrel_a.ma", "maya", 4101, "env_amy")

    # ship composes the barrel
    ship = verbs.declare(repo, sink, "set", "artist:env_amy")
    verbs.relate(repo, sink, ship, barrel, RelType.COMPOSED_OF, "env_amy")

    # production claims it later (async backfill) -> now active
    verbs.claim(repo, sink, barrel, "Barrel, Weathered Oak", "props/containers/barrel", "prod:pat")
    assert verbs.resolve(repo, barrel)["meta"].lifecycle == Lifecycle.ACTIVE
    assert verbs.resolve(repo, barrel)["identity"].display_name == "Barrel, Weathered Oak"

    # castle REUSES the same barrel — no duplicate identity
    castle = verbs.declare(repo, sink, "set", "artist:env_ben")
    verbs.relate(repo, sink, castle, barrel, RelType.COMPOSED_OF, "env_ben")

    # mossy variant: new identity, DERIVED_FROM the original
    mossy = verbs.declare(repo, sink, "prop", "artist:env_ben")
    verbs.relate(repo, sink, mossy, barrel, RelType.DERIVED_FROM, "env_ben")

    used = {u.rel_type for u in verbs.used_by(repo, barrel)}
    assert RelType.COMPOSED_OF in used and RelType.DERIVED_FROM in used
    # exactly two sets compose the SAME barrel — proving reuse, not copy
    composers = [u for u in verbs.used_by(repo, barrel) if u.rel_type == RelType.COMPOSED_OF]
    assert len(composers) == 2

    # lineage of mossy points back to the original barrel
    lin = verbs.lineage(repo, mossy)
    assert lin[0].to_asset == barrel and lin[0].rel_type == RelType.DERIVED_FROM


def scenario_robin_locomotion_semantics(repo, sink):
    bat_set = verbs.declare(repo, sink, "locomotion_set", "anim_kay")
    bat_walk = verbs.declare(repo, sink, "anim", "anim_kay")
    bat_grap = verbs.declare(repo, sink, "anim", "anim_kay")
    for a in (bat_walk, bat_grap):
        verbs.relate(repo, sink, bat_set, a, RelType.COMPOSED_OF, "anim_kay")

    rob_set = verbs.declare(repo, sink, "locomotion_set", "anim_lee")
    rob_grap = verbs.declare(repo, sink, "anim", "anim_lee")
    rob_cape = verbs.declare(repo, sink, "anim", "anim_lee")

    # robin's set: walk is SHARED (composes batman's identity directly),
    # grapple is FORKED, cape is unique
    verbs.relate(repo, sink, rob_set, bat_walk, RelType.COMPOSED_OF, "anim_lee")   # live shared
    verbs.relate(repo, sink, rob_set, rob_grap, RelType.COMPOSED_OF, "anim_lee")
    verbs.relate(repo, sink, rob_set, rob_cape, RelType.COMPOSED_OF, "anim_lee")
    verbs.relate(repo, sink, rob_grap, bat_grap, RelType.DERIVED_FROM, "anim_lee")  # forked

    # fixing batman's walk impacts BOTH sets (they share the identity)
    affected = {u.from_asset for u in verbs.used_by(repo, bat_walk)}
    assert bat_set in affected and rob_set in affected

    # robin's grapple has lineage to batman's; cape has none
    assert verbs.lineage(repo, rob_grap)[0].to_asset == bat_grap
    assert verbs.lineage(repo, rob_cape) == []


def scenario_materials_float_then_pin(repo, sink):
    mat = verbs.declare(repo, sink, "material", "mat_mo")
    verbs.bind_source(repo, sink, mat, "//depot/mat/face.sbsar", "substance", 5000, "mat_mo")

    anim = verbs.declare(repo, sink, "anim", "anim_lee")
    # float during blocking
    verbs.relate(repo, sink, anim, mat, RelType.DEPENDS_ON, "anim_lee", binding_mode=BindingMode.FLOAT)
    assert repo.get_edge(anim, mat, RelType.DEPENDS_ON).binding_mode == BindingMode.FLOAT
    assert verbs.resolve_dependency(repo, anim, mat).version_num == 1

    # materials publishes v2 — floating consumer sees it, no rebuild chain
    verbs.bind_source(repo, sink, mat, "//depot/mat/face.sbsar", "substance", 5050, "mat_mo")
    assert verbs.resolve_dependency(repo, anim, mat).version_num == 2

    # pin before delivery; later v3 is ignored  (set_binding flips the existing edge)
    verbs.set_binding(repo, sink, anim, mat, BindingMode.PIN, pinned_version=2)
    verbs.bind_source(repo, sink, mat, "//depot/mat/face.sbsar", "substance", 5099, "mat_mo")
    assert repo.get_edge(anim, mat, RelType.DEPENDS_ON).binding_mode == BindingMode.PIN
    assert verbs.resolve_dependency(repo, anim, mat).version_num == 2


def scenario_resolve_all_three_facets(repo, sink):
    a = verbs.declare(repo, sink, "prop", "env_amy")
    verbs.bind_source(repo, sink, a, "//depot/props/barrel.ma", "maya", 4101, "env_amy")
    verbs.claim(repo, sink, a, "Barrel", "props/barrel", "pat")
    verbs.bind_runtime(repo, sink, a, "/Game/Junk/Bob/BP_Barrel_FINAL", "build_8821")

    got = verbs.resolve(repo, a)
    assert got["identity"].display_name == "Barrel"
    assert got["source"].location_uri.endswith("barrel.ma")
    assert got["runtime"].location_uri == "/Game/Junk/Bob/BP_Barrel_FINAL"


def invariant_rename_does_not_touch_source_or_runtime(repo, sink):
    a = verbs.declare(repo, sink, "prop", "env_amy")
    verbs.bind_source(repo, sink, a, "//depot/props/barrel.ma", "maya", 4101, "env_amy")
    verbs.bind_runtime(repo, sink, a, "/Game/Props/Barrel", "build_1")
    before = verbs.resolve(repo, a)
    # snapshot COPIES — InMemoryRepo returns live objects, so comparing the same
    # reference would pass trivially; a copy makes this a real value comparison on
    # every backend.
    before_source = dataclasses.replace(before["source"])
    before_runtime = dataclasses.replace(before["runtime"])

    verbs.rename(repo, sink, a, "Totally Different Name", "pat", new_taxonomy="props/new/place")
    after = verbs.resolve(repo, a)

    assert after["identity"].display_name == "Totally Different Name"
    assert after["source"] == before_source      # source pointer untouched
    assert after["runtime"] == before_runtime    # runtime pointer untouched


def invariant_single_latest_source_version(repo, sink):
    a = verbs.declare(repo, sink, "prop", "env_amy")
    verbs.bind_source(repo, sink, a, "//depot/v1.ma", "maya", 1, "amy")
    verbs.bind_source(repo, sink, a, "//depot/v2.ma", "maya", 2, "amy")
    verbs.bind_source(repo, sink, a, "//depot/v3.ma", "maya", 3, "amy")
    latest = [v for v in repo.source_versions(a) if v.is_latest]
    assert len(latest) == 1 and latest[0].version_num == 3


def spine_records_declares_and_writes(repo, sink):
    a = verbs.declare(repo, sink, "prop", "env_amy")
    verbs.bind_source(repo, sink, a, "//depot/props/barrel.ma", "maya", 4101, "env_amy")
    verbs.claim(repo, sink, a, "Barrel", "props/barrel", "pat")

    assert [e.event_type for e in sink.events] == [
        "declared", "source.published", "identity.claimed",
    ]


# The full backend-agnostic suite, as (name, fn) pairs — imported by both the
# unit and integration test modules so neither duplicates the scenario logic.
ALL_SCENARIOS = [
    scenario_barrel_reuse_and_lineage,
    scenario_robin_locomotion_semantics,
    scenario_materials_float_then_pin,
    scenario_resolve_all_three_facets,
    invariant_rename_does_not_touch_source_or_runtime,
    invariant_single_latest_source_version,
    spine_records_declares_and_writes,
]
