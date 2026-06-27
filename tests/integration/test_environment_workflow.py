"""Environment workflow at scale — the production reality, proven end to end.

Builds a realistic environment (a modular kit + dozens of props reused across
several districts composed into a city, a master material instanced by every prop)
and asserts the whole lifecycle holds on BOTH backends:
  - reuse, not copy (one prop in many districts)
  - transitive impact ("what breaks if I touch the master material / a prop")
  - selective propagation (floating levels see a new material version; a shipped
    level pinned to v1 does not)
  - directory relocate (move the whole prop dir — identity + every edge intact)
  - rename-for-IP (identity facet only; source/edges untouched)
  - derivation staleness (re-sculpt -> bake flagged)
  - deprecate (retire without losing facets or consumers)

This is the env-first proving step on the Phase-11 foundation.
"""
import pytest

from assetcore.app import verbs
from assetcore.core.types import BindingMode, Lifecycle, RelType
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink
from assetcore.infra.sqlite_repo import SqliteRepo

KIT_PIECES = 12
PROPS = 40
DISTRICTS = 3


@pytest.fixture(params=["memory", "sqlite"])
def backend(request):
    if request.param == "memory":
        return InMemoryRepo(), InMemorySink()
    return SqliteRepo(":memory:"), InMemorySink()


def _build_world(repo, sink):
    """A modular environment at scale. Returns the handles the assertions need."""
    w = {}

    # the master material every prop instances (the heaviest reuse node)
    w["master_mat"] = verbs.declare(repo, sink, "material", "mat_mo")
    verbs.bind_source(repo, sink, w["master_mat"], "//depot/art/mat/env_master.sbsar",
                      "substance", "100", "mat_mo")

    # a kit of modular pieces (bulk) + the kit asset that composes them
    w["pieces"] = verbs.bulk_declare(repo, sink,
                                     [{"asset_type": "prop", "created_by": "env_amy"}
                                      for _ in range(KIT_PIECES)])
    w["kit"] = verbs.declare(repo, sink, "set", "env_amy")
    verbs.bulk_relate(repo, sink, [{"frm": w["kit"], "to": p, "rel_type": "COMPOSED_OF",
                                    "actor": "env_amy"} for p in w["pieces"]])

    # dozens of props (bulk), each claimed, source-bound, and FLOATing on the master
    w["props"] = verbs.bulk_declare(repo, sink,
                                    [{"asset_type": "prop", "created_by": "env_amy"}
                                     for _ in range(PROPS)])
    for i, p in enumerate(w["props"]):
        verbs.claim(repo, sink, p, f"Prop {i:02d}", "props/env/clutter", "prod:pat")
        verbs.bind_source(repo, sink, p, f"//depot/art/props/prop_{i:02d}.ma", "maya", "10", "env_amy")
        verbs.relate(repo, sink, p, w["master_mat"], RelType.DEPENDS_ON, "env_amy",
                     binding_mode=BindingMode.FLOAT)

    # a couple of VARIANT_OF props (mossy/clean) off prop 0
    w["variant"] = verbs.declare(repo, sink, "prop", "env_ben")
    verbs.relate(repo, sink, w["variant"], w["props"][0], RelType.VARIANT_OF, "env_ben")

    # districts REUSE overlapping prop ranges (the same prop in several districts) +
    # each composes the shared kit; the city composes the districts (env-of-envs)
    w["districts"] = [verbs.declare(repo, sink, "set", "env_amy") for _ in range(DISTRICTS)]
    span = PROPS // DISTRICTS
    for d, district in enumerate(w["districts"]):
        start = max(0, d * span - 5)                 # overlap the ranges -> reuse
        members = w["props"][start:start + span + 5]
        verbs.bulk_relate(repo, sink, [{"frm": district, "to": p, "rel_type": "COMPOSED_OF",
                                        "actor": "env_amy"} for p in members])
        verbs.relate(repo, sink, district, w["kit"], RelType.COMPOSED_OF, "env_amy")
    w["city"] = verbs.declare(repo, sink, "set", "env_amy")
    verbs.bulk_relate(repo, sink, [{"frm": w["city"], "to": d, "rel_type": "COMPOSED_OF",
                                    "actor": "env_amy"} for d in w["districts"]])

    # a SHIPPED level: composes a prop, but PINS the master material to v1 (locked)
    w["shipped"] = verbs.declare(repo, sink, "set", "env_amy")
    verbs.relate(repo, sink, w["shipped"], w["props"][0], RelType.COMPOSED_OF, "env_amy")
    verbs.relate(repo, sink, w["shipped"], w["master_mat"], RelType.DEPENDS_ON, "env_amy",
                 binding_mode=BindingMode.PIN, pinned_version=1)

    # a bake-pair: a prop DERIVED_FROM a high-poly sculpt
    w["sculpt"] = verbs.declare(repo, sink, "sculpt", "env_ben")
    verbs.bind_source(repo, sink, w["sculpt"], "//depot/art/hi/rock.ztl", "zbrush", "200", "env_ben")
    w["baked"] = verbs.declare(repo, sink, "prop", "env_ben")
    verbs.relate(repo, sink, w["baked"], w["sculpt"], RelType.DERIVED_FROM, "env_ben")
    return w


def test_environment_workflow(backend):
    repo, sink = backend
    w = _build_world(repo, sink)

    # --- reuse, not copy: a prop in the overlap belongs to >1 district ----------
    shared = w["props"][10]
    composers = [e for e in verbs.used_by(repo, shared) if e.rel_type == RelType.COMPOSED_OF]
    assert len(composers) >= 2, "a shared prop must be reused (composed) by multiple districts"

    # --- transitive impact: touching the master material reaches the whole tree -
    impacted = {a for a, _d, _rt in verbs.dependents(repo, w["master_mat"])}
    assert all(p in impacted for p in w["props"])          # every prop
    assert all(d in impacted for d in w["districts"])      # every district
    assert w["city"] in impacted                           # ...up to the city
    # and a single prop's impact climbs to the city (env-of-envs depth)
    assert w["city"] in {a for a, _d, _rt in verbs.dependents(repo, shared)}

    # --- selective propagation: float vs pin on a master update ----------------
    verbs.bind_source(repo, sink, w["master_mat"], "//depot/art/mat/env_master.sbsar",
                      "substance", "150", "mat_mo")          # master -> v2
    assert verbs.resolve_dependency(repo, w["props"][0], w["master_mat"]).version_num == 2  # floats
    assert verbs.resolve_dependency(repo, w["shipped"], w["master_mat"]).version_num == 1   # pinned

    # --- directory relocate: move ALL props, identity + edges + version intact --
    before_impact = {a for a, _d, _rt in verbs.dependents(repo, shared)}
    moves = [{"asset_id": p, "new_location_uri": f"//depot/art/env/props/prop_{i:02d}.ma",
              "actor": "prod:pat", "new_revision": "300"} for i, p in enumerate(w["props"])]
    assert verbs.bulk_relocate(repo, sink, moves) == PROPS
    src = verbs.resolve(repo, shared)["source"]
    assert src.location_uri.startswith("//depot/art/env/props/") and src.version_num == 1  # moved, not versioned
    assert {a for a, _d, _rt in verbs.dependents(repo, shared)} == before_impact            # graph intact
    assert verbs.resolve(repo, shared)["identity"].display_name == "Prop 10"               # identity intact

    # --- rename-for-IP: identity facet only; source + edges untouched ----------
    district = w["districts"][0]
    n_edges_before = len(verbs.dependencies(repo, district))
    verbs.rename(repo, sink, shared, "Hero Crate (renamed)", "prod:pat", new_taxonomy="props/hero")
    assert verbs.resolve(repo, shared)["identity"].display_name == "Hero Crate (renamed)"
    assert verbs.resolve(repo, shared)["source"].location_uri.startswith("//depot/art/env/props/")
    assert len(verbs.dependencies(repo, district)) == n_edges_before                        # edges intact

    # --- derivation staleness: re-sculpt flags the bake ------------------------
    assert verbs.stale_derivations(repo, w["baked"]) == []
    verbs.bind_source(repo, sink, w["sculpt"], "//depot/art/hi/rock.ztl", "zbrush", "250", "env_ben")
    stale = verbs.stale_derivations(repo, w["baked"])
    assert len(stale) == 1 and stale[0].to_asset == w["sculpt"]

    # --- deprecate: retire a prop without losing its facets or its consumers ----
    old = w["props"][PROPS - 1]
    consumers_before = {e.from_asset for e in verbs.used_by(repo, old)}
    verbs.deprecate(repo, sink, old, "prod:pat")
    assert repo.get_asset(old).lifecycle == Lifecycle.DEPRECATED
    assert verbs.resolve(repo, old)["source"] is not None                                    # facets kept
    assert {e.from_asset for e in verbs.used_by(repo, old)} == consumers_before              # still findable
