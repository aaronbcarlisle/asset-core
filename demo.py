"""
demo.py — narrated end-to-end walkthrough of assetcore (the layered stack).

Run:  python demo.py

No database or service setup required: it drives the universal verbs
(`assetcore.app.verbs`) directly over an in-memory repo + event sink, so you can
watch the model resolve each real production scenario through
declare / bind / relate / resolve with zero I/O.

(The original single-file prototype this grew from is frozen under
`examples/prototype/` — see its README.)
"""
from assetcore.app import verbs
from assetcore.core.types import BindingMode, RelType
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink


def line():
    print("-" * 72)


def name_of(repo, asset_id):
    ident = repo.get_identity(asset_id)
    return (ident.display_name if ident and ident.display_name
            else repo.get_asset(asset_id).asset_type)


def main():
    repo, sink = InMemoryRepo(), InMemorySink()

    # ======================================================================
    print("SCENARIO 1 — barrel reuse & lineage"); line()
    # ======================================================================
    barrel = verbs.declare(repo, sink, "prop", "artist:env_amy",
                           {"declared_while_on": "pirate_ship"})
    verbs.bind_source(repo, sink, barrel, "//depot/art/props/barrel_a.ma", "maya", "4101", "env_amy")
    print(f"Artist declared a barrel (provisional): {str(barrel)[:8]}…  — no production wait")

    ship = verbs.declare(repo, sink, "set", "artist:env_amy", {"name_hint": "pirate_ship"})
    verbs.relate(repo, sink, ship, barrel, RelType.COMPOSED_OF, "env_amy")

    verbs.claim(repo, sink, barrel, "Barrel, Weathered Oak", "props/containers/barrel", "prod:pat")
    print("Production later CLAIMED it ->",
          verbs.resolve(repo, barrel)["identity"].display_name)

    castle = verbs.declare(repo, sink, "set", "artist:env_ben", {"name_hint": "castle"})
    verbs.relate(repo, sink, castle, barrel, RelType.COMPOSED_OF, "env_ben")
    print("Castle reuses the SAME barrel (no duplicate created).")

    mossy = verbs.declare(repo, sink, "prop", "artist:env_ben")
    verbs.bind_source(repo, sink, mossy, "//depot/art/props/barrel_mossy.ma", "maya", "4140", "env_ben")
    verbs.relate(repo, sink, mossy, barrel, RelType.DERIVED_FROM, "env_ben")
    verbs.claim(repo, sink, mossy, "Barrel, Mossy", "props/containers/barrel", "prod:pat")

    print("\nWhere is the original barrel USED?")
    for u in verbs.used_by(repo, barrel):
        print(f"   <- {u.rel_type.value:12} {name_of(repo, u.from_asset)}")
    print("Where did the mossy barrel COME FROM?")
    for l in verbs.lineage(repo, mossy):
        print(f"   -> {l.rel_type.value:12} {name_of(repo, l.to_asset)}")

    # ======================================================================
    print("\n\nSCENARIO 2 — Robin locomotion from Batman"); line()
    # ======================================================================
    def named(asset_type, actor, display, taxonomy):
        aid = verbs.declare(repo, sink, asset_type, actor)
        verbs.claim(repo, sink, aid, display, taxonomy, "pat")
        return aid

    bat_set = named("locomotion_set", "anim_kay", "Batman Locomotion", "anim/loco/batman")
    bat_walk = named("anim", "anim_kay", "Batman Walk", "anim/loco/batman")
    bat_grap = named("anim", "anim_kay", "Batman Grapple", "anim/loco/batman")
    for a in (bat_walk, bat_grap):
        verbs.relate(repo, sink, bat_set, a, RelType.COMPOSED_OF, "anim_kay")

    rob_set = named("locomotion_set", "anim_lee", "Robin Locomotion", "anim/loco/robin")
    rob_grap = named("anim", "anim_lee", "Robin Grapple", "anim/loco/robin")
    rob_cape = named("anim", "anim_lee", "Robin Cape Twirl", "anim/loco/robin")

    verbs.relate(repo, sink, rob_set, bat_walk, RelType.COMPOSED_OF, "anim_lee")  # live shared
    verbs.relate(repo, sink, rob_set, rob_grap, RelType.COMPOSED_OF, "anim_lee")
    verbs.relate(repo, sink, rob_set, rob_cape, RelType.COMPOSED_OF, "anim_lee")
    verbs.relate(repo, sink, rob_grap, bat_grap, RelType.DERIVED_FROM, "anim_lee")  # forked

    print("Robin's locomotion set members:")
    for e in repo.edges_from(rob_set, RelType.COMPOSED_OF):
        member = e.to_asset
        lin = verbs.lineage(repo, member)
        if member == bat_walk:
            tag = "LIVE shared from Batman (fix Batman's walk, Robin inherits)"
        elif lin:
            tag = f"{lin[0].rel_type.value} {name_of(repo, lin[0].to_asset)} (forked)"
        else:
            tag = "unique to Robin"
        print(f"   • {name_of(repo, member):20} -> {tag}")

    print("\nIf we fix Batman's Walk, who's impacted?")
    for u in verbs.used_by(repo, bat_walk):
        print(f"   affected: {name_of(repo, u.from_asset)}")

    # ======================================================================
    print("\n\nSCENARIO 3 — parallel materials handoff (float vs pin)"); line()
    # ======================================================================
    mat = named("material", "mat_mo", "Captain Face Material", "mat/char/captain")
    verbs.bind_source(repo, sink, mat, "//depot/mat/captain_face.sbsar", "substance", "5000", "mat_mo")

    anim = named("anim", "anim_lee", "Captain Facial Anim", "anim/face/captain")
    verbs.relate(repo, sink, anim, mat, RelType.DEPENDS_ON, "anim_lee", binding_mode=BindingMode.FLOAT)
    r = verbs.resolve_dependency(repo, anim, mat)
    print(f"Animator FLOATS the material -> resolves v{r.version_num} (float)")

    verbs.bind_source(repo, sink, mat, "//depot/mat/captain_face.sbsar", "substance", "5050", "mat_mo")
    r = verbs.resolve_dependency(repo, anim, mat)
    print(f"Materials publishes v2 -> floating ref now resolves v{r.version_num} automatically (no rebuild chain)")

    verbs.set_binding(repo, sink, anim, mat, BindingMode.PIN, pinned_version=2)
    verbs.bind_source(repo, sink, mat, "//depot/mat/captain_face.sbsar", "substance", "5099", "mat_mo")
    r = verbs.resolve_dependency(repo, anim, mat)
    print(f"Animator PINS to v2, materials ships v3 -> resolves v{r.version_num} (pin), stable")

    # ======================================================================
    print("\n\nBONUS — 'staring at it in-editor, where's the source?'"); line()
    # ======================================================================
    verbs.bind_runtime(repo, sink, barrel, "/Game/Junk/Bob/BP_Barrel_FINAL_USETHIS", "build_8821")
    got = verbs.resolve(repo, barrel)
    print(f"Right-click barrel in editor (uuid {str(barrel)[:8]}…):")
    print(f"   identity : {got['identity'].display_name}")
    print(f"   source   : {got['source'].location_uri} v{got['source'].version_num} (rev {got['source'].revision})")
    print(f"   runtime  : {got['runtime'].location_uri}")
    print("   -> 'Open Source' is one lookup. Nobody exports-to-re-edit again.")

    print("\n\nEVENT SPINE (subscribe feed / audit), last 6:"); line()
    for e in sink.events[-6:]:
        print(f"   {e.event_type:22} {e.actor or ''}")


if __name__ == "__main__":
    main()
