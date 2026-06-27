"""Animation workflow — locomotion sets, shared vs forked clips, propagation, IP rename.

The character-animation reality the research surfaced, proven on BOTH backends:
  - a shared skeleton many clips DEPENDS_ON (the high-blast-radius hub)
  - locomotion sets COMPOSED_OF clips, with one walk cycle a member of MANY sets
    (many-to-many shared membership)
  - SHARED vs FORKED: a shared clip propagates live (referenced by identity); a
    forked clip (DERIVED_FROM) goes stale and must be re-propagated, flagged by
    stale_derivations
  - skeleton blast radius via transitive dependents ("what breaks if I touch it")
  - rename-for-IP (Batman -> Nightwing): identity changes, the bytes and every set
    membership that references the identity are untouched

Companion to the environment workflow; same Phase-11 foundation.
"""
import pytest

from assetcore.app import verbs
from assetcore.core.types import BindingMode, RelType
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink
from assetcore.infra.sqlite_repo import SqliteRepo


@pytest.fixture(params=["memory", "sqlite"])
def backend(request):
    if request.param == "memory":
        return InMemoryRepo(), InMemorySink()
    return SqliteRepo(":memory:"), InMemorySink()


def _clip(repo, sink, name, rev="10"):
    c = verbs.declare(repo, sink, "anim", "anim_kay")
    verbs.claim(repo, sink, c, name, "anim/locomotion", "prod:pat")
    verbs.bind_source(repo, sink, c, f"//depot/anim/{name.replace(' ', '_').lower()}.ma",
                      "maya", rev, "anim_kay")
    return c


def _build_cast(repo, sink):
    w = {}
    # one shared humanoid skeleton the whole cast animates on
    w["skeleton"] = verbs.declare(repo, sink, "rig", "rig_ray")
    verbs.bind_source(repo, sink, w["skeleton"], "//depot/rigs/hero_skeleton.ma", "maya", "5", "rig_ray")

    # Batman: a character composing a locomotion set of clips; every clip depends on
    # the shared skeleton
    w["bat_walk"] = _clip(repo, sink, "Batman Walk")
    w["bat_run"] = _clip(repo, sink, "Batman Run")
    w["bat_grapple"] = _clip(repo, sink, "Batman Grapple")
    w["bat_set"] = verbs.declare(repo, sink, "locomotion_set", "anim_kay")
    for clip in (w["bat_walk"], w["bat_run"], w["bat_grapple"]):
        verbs.relate(repo, sink, w["bat_set"], clip, RelType.COMPOSED_OF, "anim_kay")
        verbs.relate(repo, sink, clip, w["skeleton"], RelType.DEPENDS_ON, "anim_kay",
                     binding_mode=BindingMode.FLOAT)
    w["batman"] = verbs.declare(repo, sink, "character", "anim_kay")
    verbs.relate(repo, sink, w["batman"], w["bat_set"], RelType.COMPOSED_OF, "anim_kay")

    # Robin: reuses Batman's walk (SHARED, live), forks his grapple (DERIVED_FROM),
    # and has a unique cape clip
    w["rob_grapple"] = _clip(repo, sink, "Robin Grapple")
    verbs.relate(repo, sink, w["rob_grapple"], w["bat_grapple"], RelType.DERIVED_FROM, "anim_lee")
    w["rob_cape"] = _clip(repo, sink, "Robin Cape")
    w["rob_set"] = verbs.declare(repo, sink, "locomotion_set", "anim_lee")
    for clip in (w["bat_walk"], w["rob_grapple"], w["rob_cape"]):   # bat_walk SHARED into Robin's set
        verbs.relate(repo, sink, w["rob_set"], clip, RelType.COMPOSED_OF, "anim_lee")
    verbs.relate(repo, sink, w["rob_cape"], w["skeleton"], RelType.DEPENDS_ON, "anim_lee",
                 binding_mode=BindingMode.FLOAT)
    w["robin"] = verbs.declare(repo, sink, "character", "anim_lee")
    verbs.relate(repo, sink, w["robin"], w["rob_set"], RelType.COMPOSED_OF, "anim_lee")
    return w


def test_animation_workflow(backend):
    repo, sink = backend
    w = _build_cast(repo, sink)

    # --- many-to-many: one walk cycle is a member of BOTH locomotion sets -------
    walk_sets = {e.from_asset for e in verbs.used_by(repo, w["bat_walk"])
                 if e.rel_type == RelType.COMPOSED_OF}
    assert walk_sets == {w["bat_set"], w["rob_set"]}     # shared, not copied

    # --- SHARED propagates live: fix the walk, both sets reference the new version
    verbs.bind_source(repo, sink, w["bat_walk"], "//depot/anim/batman_walk.ma", "maya", "11", "anim_kay")
    assert verbs.resolve(repo, w["bat_walk"])["source"].version_num == 2
    impacted = {a for a, _d, _rt in verbs.dependents(repo, w["bat_walk"])}
    assert {w["bat_set"], w["rob_set"], w["batman"], w["robin"]} <= impacted   # both characters

    # --- FORKED goes stale: re-animating Batman's grapple flags Robin's fork ----
    assert verbs.stale_derivations(repo, w["rob_grapple"]) == []
    verbs.bind_source(repo, sink, w["bat_grapple"], "//depot/anim/batman_grapple.ma", "maya", "12", "anim_kay")
    stale = verbs.stale_derivations(repo, w["rob_grapple"])
    assert len(stale) == 1 and stale[0].to_asset == w["bat_grapple"]   # fork needs re-propagation

    # --- skeleton blast radius: touching the shared rig reaches the whole cast --
    rig_impact = {a for a, _d, _rt in verbs.dependents(repo, w["skeleton"])}
    assert {w["bat_walk"], w["bat_run"], w["bat_grapple"], w["rob_cape"]} <= rig_impact  # clips
    assert {w["bat_set"], w["rob_set"], w["batman"], w["robin"]} <= rig_impact           # ...up to chars

    # --- rename-for-IP: Batman -> Nightwing; identity changes, links survive -----
    src_before = verbs.resolve(repo, w["bat_walk"])["source"].location_uri
    verbs.rename(repo, sink, w["bat_walk"], "Nightwing Walk", "prod:pat")
    verbs.rename(repo, sink, w["batman"], "Nightwing", "prod:pat")
    assert verbs.resolve(repo, w["bat_walk"])["identity"].display_name == "Nightwing Walk"
    assert verbs.resolve(repo, w["bat_walk"])["source"].location_uri == src_before   # bytes untouched
    # both sets still compose the (renamed) walk — they referenced the IDENTITY, not the name
    walk_sets_after = {e.from_asset for e in verbs.used_by(repo, w["bat_walk"])
                       if e.rel_type == RelType.COMPOSED_OF}
    assert walk_sets_after == {w["bat_set"], w["rob_set"]}
