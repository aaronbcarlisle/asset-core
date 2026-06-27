"""Phase-16 core gaps: transitive graph queries, relocate, deprecate, staleness, bulk.

Pure-rule tests + verb tests against InMemoryRepo (the heart of the new capability;
the SQL backends run the same behaviour via the shared scenario suite).
"""
from assetcore.app import verbs
from assetcore.core import rules
from assetcore.core.entities import Asset, IdentityFacet
from assetcore.core.types import Lifecycle, RelType
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink


# --- pure rules ------------------------------------------------------------
def test_walk_closure_bfs_order_and_cycle_safe():
    graph = {1: [(2, "a"), (3, "a")], 2: [(4, "b")], 3: [(4, "b")], 4: [(1, "c")]}
    reached = rules.walk_closure(1, lambda n: graph.get(n, []))
    nodes = [n for n, _d, _l in reached]
    assert set(nodes) == {2, 3, 4}      # 1 (start) excluded; 4 reached once despite two paths
    assert nodes.count(4) == 1          # cycle back to 1 doesn't loop
    depth = {n: d for n, d, _ in reached}
    assert depth[2] == 1 and depth[3] == 1 and depth[4] == 2


def test_walk_closure_max_depth():
    graph = {1: [(2, "x")], 2: [(3, "x")], 3: [(4, "x")]}
    reached = rules.walk_closure(1, lambda n: graph.get(n, []), max_depth=1)
    assert [n for n, _d, _l in reached] == [2]


def test_derivation_is_stale_rule():
    assert rules.derivation_is_stale(1, 2) is True
    assert rules.derivation_is_stale(2, 2) is False
    assert rules.derivation_is_stale(None, 5) is False   # legacy edge: never guess
    assert rules.derivation_is_stale(1, None) is False


# --- transitive dependents / dependencies ----------------------------------
def _env_graph():
    repo, sink = InMemoryRepo(), InMemorySink()
    city = verbs.declare(repo, sink, "set", "amy")
    district = verbs.declare(repo, sink, "set", "amy")
    prop = verbs.declare(repo, sink, "prop", "amy")
    verbs.relate(repo, sink, city, district, RelType.COMPOSED_OF, "amy")
    verbs.relate(repo, sink, district, prop, RelType.COMPOSED_OF, "amy")
    return repo, sink, city, district, prop


def test_dependents_transitive_impact():
    repo, _s, city, district, prop = _env_graph()
    dep = verbs.dependents(repo, prop)
    by_id = {a: (d, rt) for a, d, rt in dep}
    assert set(by_id) == {district, city}          # both the district and the city are impacted
    assert by_id[district][0] == 1 and by_id[city][0] == 2   # with correct depths


def test_dependencies_transitive():
    repo, _s, city, district, prop = _env_graph()
    deps = verbs.dependencies(repo, city)
    by_id = {a: d for a, d, _rt in deps}
    assert by_id == {district: 1, prop: 2}


def test_dependents_reltype_filter_and_depth():
    repo, _s, city, district, prop = _env_graph()
    assert {a for a, _d, _rt in verbs.dependents(repo, prop, max_depth=1)} == {district}
    assert verbs.dependents(repo, prop, rel_types=["DEPENDS_ON"]) == []   # no such edges


# --- relocate: bytes move, identity + edges + version untouched ------------
def test_relocate_source_keeps_identity_edges_and_version():
    repo, sink = InMemoryRepo(), InMemorySink()
    a = verbs.declare(repo, sink, "prop", "amy")
    other = verbs.declare(repo, sink, "set", "amy")
    verbs.bind_source(repo, sink, a, "//depot/old/barrel.ma", "maya", "10", "amy")
    verbs.claim(repo, sink, a, "Barrel", "props/barrel", "pat")
    verbs.relate(repo, sink, other, a, RelType.COMPOSED_OF, "amy")

    verbs.relocate(repo, sink, a, "//depot/new/env/barrel.ma", "pat", new_revision="42")

    src = next(v for v in repo.source_versions(a) if v.is_latest)
    assert src.location_uri == "//depot/new/env/barrel.ma" and src.revision == "42"
    assert len(repo.source_versions(a)) == 1 and src.version_num == 1   # NOT a new version
    assert repo.get_identity(a).display_name == "Barrel"                # identity untouched
    assert repo.get_edge(other, a, RelType.COMPOSED_OF) is not None     # edge intact
    assert [e.event_type for e in sink.events][-1] == "source.relocated"


def test_relocate_runtime_and_missing_facet():
    repo, sink = InMemoryRepo(), InMemorySink()
    a = verbs.declare(repo, sink, "prop", "amy")
    verbs.bind_runtime(repo, sink, a, "/Game/Old/BP", "build1")
    verbs.relocate(repo, sink, a, "/Game/New/BP", "eng", facet="runtime")
    assert next(v for v in repo.runtime_versions(a) if v.is_latest).location_uri == "/Game/New/BP"

    b = verbs.declare(repo, sink, "prop", "amy")   # no source facet yet
    try:
        verbs.relocate(repo, sink, b, "//depot/x.ma", "pat")
        assert False, "expected ValueError relocating a non-existent facet"
    except ValueError:
        pass


# --- deprecate -------------------------------------------------------------
def test_deprecate_sets_lifecycle_and_keeps_facets():
    repo, sink = InMemoryRepo(), InMemorySink()
    a = verbs.declare(repo, sink, "prop", "amy")
    verbs.claim(repo, sink, a, "Old Prop", "props/x", "pat")
    verbs.deprecate(repo, sink, a, "pat")
    assert repo.get_asset(a).lifecycle == Lifecycle.DEPRECATED
    assert verbs.resolve(repo, a)["identity"].display_name == "Old Prop"   # not deleted
    assert [e.event_type for e in sink.events][-1] == "identity.deprecated"


# --- staleness -------------------------------------------------------------
def test_stale_derivations_flags_after_parent_advances():
    repo, sink = InMemoryRepo(), InMemorySink()
    hi = verbs.declare(repo, sink, "sculpt", "amy")
    low = verbs.declare(repo, sink, "mesh", "amy")
    verbs.bind_source(repo, sink, hi, "//depot/hi.ztl", "zbrush", "1", "amy")   # hi v1
    verbs.relate(repo, sink, low, hi, RelType.DERIVED_FROM, "amy")              # derived at v1
    assert verbs.stale_derivations(repo, low) == []                            # fresh

    verbs.bind_source(repo, sink, hi, "//depot/hi.ztl", "zbrush", "2", "amy")  # hi re-sculpted -> v2
    stale = verbs.stale_derivations(repo, low)
    assert len(stale) == 1 and stale[0].to_asset == hi                         # bake now stale


def test_derived_from_without_parent_source_is_never_stale():
    repo, sink = InMemoryRepo(), InMemorySink()
    parent = verbs.declare(repo, sink, "concept", "amy")
    child = verbs.declare(repo, sink, "model", "amy")
    verbs.relate(repo, sink, child, parent, RelType.DERIVED_FROM, "amy")   # parent has no source
    verbs.bind_source(repo, sink, parent, "//depot/c.psd", "photoshop", "1", "amy")
    assert verbs.stale_derivations(repo, child) == []   # no derive-version anchor -> not guessed


# --- bulk ------------------------------------------------------------------
def test_bulk_declare_relate_relocate():
    repo, sink = InMemoryRepo(), InMemorySink()
    ids = verbs.bulk_declare(repo, sink, [{"asset_type": "prop", "created_by": "amy"}
                                          for _ in range(5)])
    assert len(ids) == 5 and len(set(ids)) == 5
    env = verbs.declare(repo, sink, "set", "amy")
    n = verbs.bulk_relate(repo, sink, [{"frm": env, "to": pid,
                                        "rel_type": "COMPOSED_OF", "actor": "amy"} for pid in ids])
    assert n == 5 and len(verbs.dependencies(repo, env)) == 5

    for pid in ids:
        verbs.bind_source(repo, sink, pid, f"//depot/old/{pid}.ma", "maya", "1", "amy")
    moves = [{"asset_id": pid, "new_location_uri": f"//depot/new/{pid}.ma", "actor": "pat"}
             for pid in ids]
    assert verbs.bulk_relocate(repo, sink, moves) == 5
    assert all(next(v for v in repo.source_versions(pid) if v.is_latest)
               .location_uri.startswith("//depot/new/") for pid in ids)
