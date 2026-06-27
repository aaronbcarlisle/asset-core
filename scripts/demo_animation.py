"""Narrated animation-workflow demo (in-memory, zero setup):

    python scripts/demo_animation.py

The character-animation story: a shared skeleton, locomotion sets that reuse a
single walk cycle (shared, live) vs fork a grapple (DERIVED_FROM, must re-propagate),
the skeleton's blast radius, and a Batman -> Nightwing rename that keeps every set
membership because they reference the IDENTITY, not the name.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assetcore.app import verbs
from assetcore.core.types import BindingMode, RelType
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink

repo, sink = InMemoryRepo(), InMemorySink()


def say(m):
    print(m)


def clip(name):
    c = verbs.declare(repo, sink, "anim", "anim_kay")
    verbs.claim(repo, sink, c, name, "anim/locomotion", "prod:pat")
    verbs.bind_source(repo, sink, c, f"//depot/anim/{name.replace(' ', '_').lower()}.ma",
                      "maya", "10", "anim_kay")
    return c


say("\n== A shared skeleton + two characters ==")
skeleton = verbs.declare(repo, sink, "rig", "rig_ray")
verbs.bind_source(repo, sink, skeleton, "//depot/rigs/hero.ma", "maya", "5", "rig_ray")
bat_walk, bat_run, bat_grapple = clip("Batman Walk"), clip("Batman Run"), clip("Batman Grapple")
bat_set = verbs.declare(repo, sink, "locomotion_set", "anim_kay")
for c in (bat_walk, bat_run, bat_grapple):
    verbs.relate(repo, sink, bat_set, c, RelType.COMPOSED_OF, "anim_kay")
    verbs.relate(repo, sink, c, skeleton, RelType.DEPENDS_ON, "anim_kay", binding_mode=BindingMode.FLOAT)
batman = verbs.declare(repo, sink, "character", "anim_kay")
verbs.relate(repo, sink, batman, bat_set, RelType.COMPOSED_OF, "anim_kay")

rob_grapple, rob_cape = clip("Robin Grapple"), clip("Robin Cape")
verbs.relate(repo, sink, rob_grapple, bat_grapple, RelType.DERIVED_FROM, "anim_lee")   # forked
rob_set = verbs.declare(repo, sink, "locomotion_set", "anim_lee")
for c in (bat_walk, rob_grapple, rob_cape):                                            # bat_walk SHARED
    verbs.relate(repo, sink, rob_set, c, RelType.COMPOSED_OF, "anim_lee")
robin = verbs.declare(repo, sink, "character", "anim_lee")
verbs.relate(repo, sink, robin, rob_set, RelType.COMPOSED_OF, "anim_lee")
say("  Batman's walk is SHARED into Robin's set; Robin's grapple is FORKED from Batman's")

say("\n== Many-to-many: one walk, many sets ==")
sets = [e.from_asset for e in verbs.used_by(repo, bat_walk) if e.rel_type == RelType.COMPOSED_OF]
say(f"  the walk cycle is a member of {len(sets)} locomotion sets (shared, not copied)")

say("\n== Fix the shared walk -> every set follows live ==")
verbs.bind_source(repo, sink, bat_walk, "//depot/anim/batman_walk.ma", "maya", "11", "anim_kay")
reached = {a for a, _d, _rt in verbs.dependents(repo, bat_walk)}
say(f"  walk -> v{verbs.resolve(repo, bat_walk)['source'].version_num}; impact reaches both characters: "
    f"{batman in reached and robin in reached}")

say("\n== Fork the grapple -> Robin's copy goes stale (manual re-propagation) ==")
verbs.bind_source(repo, sink, bat_grapple, "//depot/anim/batman_grapple.ma", "maya", "12", "anim_kay")
stale = verbs.stale_derivations(repo, rob_grapple)
say(f"  re-animated Batman's grapple -> Robin's fork stale: {bool(stale)} "
    f"(shared auto-propagates; forked must be re-derived)")

say("\n== Skeleton blast radius ==")
rig_impact = verbs.dependents(repo, skeleton)
say(f"  touching the shared skeleton impacts {len(rig_impact)} assets "
    f"(every clip -> set -> character) — see it before you do it")

say("\n== Batman -> Nightwing (IP rename): identity changes, links survive ==")
verbs.rename(repo, sink, bat_walk, "Nightwing Walk", "prod:pat")
verbs.rename(repo, sink, batman, "Nightwing", "prod:pat")
sets_after = [e.from_asset for e in verbs.used_by(repo, bat_walk) if e.rel_type == RelType.COMPOSED_OF]
say(f"  renamed to '{verbs.resolve(repo, bat_walk)['identity'].display_name}'; "
    f"still in {len(sets_after)} sets, source unchanged ({verbs.resolve(repo, bat_walk)['source'].location_uri})")

say(f"\n[OK] {len(sink.events)} events on the spine. Reuse is by identity, so a rename never breaks a set.\n")
