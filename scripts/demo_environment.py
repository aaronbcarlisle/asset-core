"""Narrated environment-workflow demo (in-memory, zero setup):

    python scripts/demo_environment.py

Tells the production story the test asserts: a modular kit + props reused across
districts composed into a city, a master material instanced by every prop, then the
operations that usually hurt — a master update (float vs pinned), a directory
relocate, an IP rename, and a retire — all with identity (UUID) never moving.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assetcore.app import verbs
from assetcore.core.types import BindingMode, RelType
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink

repo, sink = InMemoryRepo(), InMemorySink()
PROPS = 30


def say(msg):
    print(msg)


say("\n== Build a modular environment ==")
master = verbs.declare(repo, sink, "material", "mat_mo")
verbs.bind_source(repo, sink, master, "//depot/art/mat/master.sbsar", "substance", "100", "mat_mo")
props = verbs.bulk_declare(repo, sink, [{"asset_type": "prop", "created_by": "env_amy"}
                                        for _ in range(PROPS)])
for i, p in enumerate(props):
    verbs.claim(repo, sink, p, f"Prop {i:02d}", "props/env", "prod:pat")
    verbs.bind_source(repo, sink, p, f"//depot/art/props/prop_{i:02d}.ma", "maya", "10", "env_amy")
    verbs.relate(repo, sink, p, master, RelType.DEPENDS_ON, "env_amy", binding_mode=BindingMode.FLOAT)
say(f"  declared 1 master material + {PROPS} props (each floats on the master)")

districts = [verbs.declare(repo, sink, "set", "env_amy") for _ in range(3)]
for d, district in enumerate(districts):
    members = props[max(0, d * 10 - 4):d * 10 + 14]          # overlapping -> reuse
    verbs.bulk_relate(repo, sink, [{"frm": district, "to": p, "rel_type": "COMPOSED_OF",
                                    "actor": "env_amy"} for p in members])
city = verbs.declare(repo, sink, "set", "env_amy")
verbs.bulk_relate(repo, sink, [{"frm": city, "to": d, "rel_type": "COMPOSED_OF", "actor": "env_amy"}
                               for d in districts])
say("  composed 3 districts (reusing overlapping props) into 1 city")

shipped = verbs.declare(repo, sink, "set", "prod:pat")
verbs.relate(repo, sink, shipped, master, RelType.DEPENDS_ON, "prod:pat",
             binding_mode=BindingMode.PIN, pinned_version=1)
say("  shipped a level that PINS the master material to v1")

say("\n== Reuse, not copy ==")
shared = props[8]
reused_by = [e for e in verbs.used_by(repo, shared) if e.rel_type == RelType.COMPOSED_OF]
say(f"  Prop 08 is composed by {len(reused_by)} districts — one identity, many uses")

say("\n== Transitive impact: 'what breaks if I touch the master material?' ==")
impact = verbs.dependents(repo, master)
depths = sorted({d for _a, d, _rt in impact})
say(f"  {len(impact)} assets impacted, across depths {depths} (props -> districts -> city)")

say("\n== Master update: floating levels follow, the shipped level holds ==")
verbs.bind_source(repo, sink, master, "//depot/art/mat/master.sbsar", "substance", "150", "mat_mo")
say(f"  dev prop sees master v{verbs.resolve_dependency(repo, props[0], master).version_num}"
    f"  |  shipped level still pinned to v{verbs.resolve_dependency(repo, shipped, master).version_num}")

say("\n== Art reorg: relocate the whole prop directory ==")
verbs.bulk_relocate(repo, sink, [{"asset_id": p, "actor": "prod:pat", "new_revision": "300",
                                  "new_location_uri": f"//depot/art/env/props/prop_{i:02d}.ma"}
                                 for i, p in enumerate(props)])
src = verbs.resolve(repo, shared)["source"]
say(f"  moved {PROPS} props -> {src.location_uri}  (still source v{src.version_num}, identity unchanged)")
say(f"  impact graph after the move: {len(verbs.dependents(repo, shared))} consumers still resolve")

say("\n== Rename for IP: identity changes, bytes and edges do not ==")
verbs.rename(repo, sink, shared, "Hero Crate", "prod:pat", new_taxonomy="props/hero")
r = verbs.resolve(repo, shared)
say(f"  '{r['identity'].display_name}'  source still {r['source'].location_uri}")

say("\n== Retire a superseded prop (reversible, keeps consumers) ==")
old = props[PROPS - 1]
verbs.deprecate(repo, sink, old, "prod:pat")
say(f"  Prop {PROPS-1:02d} -> {repo.get_asset(old).lifecycle.value}; "
    f"still used_by {len(verbs.used_by(repo, old))} consumer(s) (retire is safe, not a delete)")

say(f"\n[OK] {len(sink.events)} events on the spine. One UUID per asset, throughout.\n")
