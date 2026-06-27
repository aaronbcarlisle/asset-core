"""
demo.py — narrated end-to-end walkthrough of assetcore.

Run:  python demo.py

No database setup required (uses in-memory SQLite). This is the same logic as the
test suite, but printed as a story so you can watch the model resolve each real
production scenario through declare / bind / relate / resolve.
"""
from assetcore import api
from assetcore.db.connection import SqliteDB


def line():
    print("-" * 72)


def main():
    db = SqliteDB()

    # ======================================================================
    print("SCENARIO 1 — barrel reuse & lineage"); line()
    # ======================================================================
    barrel = api.declare(db, "prop", "artist:env_amy",
                         {"declared_while_on": "pirate_ship"})
    api.bind_source(db, barrel, "//depot/art/props/barrel_a.ma", "maya", 4101, "env_amy")
    print(f"Artist declared a barrel (provisional): {barrel[:8]}…  — no production wait")

    ship = api.declare(db, "set", "artist:env_amy", {"name_hint": "pirate_ship"})
    api.relate(db, ship, barrel, "COMPOSED_OF", "env_amy")

    api.claim(db, barrel, "Barrel, Weathered Oak", "props/containers/barrel", "prod:pat")
    print("Production later CLAIMED it ->",
          api.resolve(db, barrel)["identity"]["display_name"])

    castle = api.declare(db, "set", "artist:env_ben", {"name_hint": "castle"})
    api.relate(db, castle, barrel, "COMPOSED_OF", "env_ben")
    print("Castle reuses the SAME barrel (no duplicate created).")

    mossy = api.declare(db, "prop", "artist:env_ben")
    api.bind_source(db, mossy, "//depot/art/props/barrel_mossy.ma", "maya", 4140, "env_ben")
    api.relate(db, mossy, barrel, "DERIVED_FROM", "env_ben")
    api.claim(db, mossy, "Barrel, Mossy", "props/containers/barrel", "prod:pat")

    print("\nWhere is the original barrel USED?")
    for u in api.used_by(db, barrel):
        info = api.resolve(db, u["from_asset"])
        label = info["identity"]["display_name"] or info["meta"]["asset_type"]
        print(f"   <- {u['rel_type']:12} {label}")
    print("Where did the mossy barrel COME FROM?")
    for l in api.lineage(db, mossy):
        print(f"   -> {l['rel_type']:12} "
              f"{api.resolve(db, l['to_asset'])['identity']['display_name']}")

    # ======================================================================
    print("\n\nSCENARIO 2 — Robin locomotion from Batman"); line()
    # ======================================================================
    bat_set = api.declare(db, "locomotion_set", "anim_kay"); api.claim(db, bat_set, "Batman Locomotion", "anim/loco/batman", "pat")
    bat_walk = api.declare(db, "anim", "anim_kay"); api.claim(db, bat_walk, "Batman Walk", "anim/loco/batman", "pat")
    bat_grap = api.declare(db, "anim", "anim_kay"); api.claim(db, bat_grap, "Batman Grapple", "anim/loco/batman", "pat")
    for a in (bat_walk, bat_grap):
        api.relate(db, bat_set, a, "COMPOSED_OF", "anim_kay")

    rob_set = api.declare(db, "locomotion_set", "anim_lee"); api.claim(db, rob_set, "Robin Locomotion", "anim/loco/robin", "pat")
    rob_grap = api.declare(db, "anim", "anim_lee"); api.claim(db, rob_grap, "Robin Grapple", "anim/loco/robin", "pat")
    rob_cape = api.declare(db, "anim", "anim_lee"); api.claim(db, rob_cape, "Robin Cape Twirl", "anim/loco/robin", "pat")

    api.relate(db, rob_set, bat_walk, "COMPOSED_OF", "anim_lee")  # live shared
    api.relate(db, rob_set, rob_grap, "COMPOSED_OF", "anim_lee")
    api.relate(db, rob_set, rob_cape, "COMPOSED_OF", "anim_lee")
    api.relate(db, rob_grap, bat_grap, "DERIVED_FROM", "anim_lee")  # forked

    print("Robin's locomotion set members:")
    members = db.fetchall(
        "SELECT to_asset FROM relationship WHERE from_asset=? AND rel_type='COMPOSED_OF'",
        (rob_set,))
    for m in members:
        info = api.resolve(db, m["to_asset"]); name = info["identity"]["display_name"]
        lin = api.lineage(db, m["to_asset"])
        if m["to_asset"] == bat_walk:
            tag = "LIVE shared from Batman (fix Batman's walk, Robin inherits)"
        elif lin:
            tag = f"{lin[0]['rel_type']} {api.resolve(db, lin[0]['to_asset'])['identity']['display_name']} (forked)"
        else:
            tag = "unique to Robin"
        print(f"   • {name:20} -> {tag}")

    print("\nIf we fix Batman's Walk, who's impacted?")
    for u in api.used_by(db, bat_walk):
        print(f"   affected: {api.resolve(db, u['from_asset'])['identity']['display_name']}")

    # ======================================================================
    print("\n\nSCENARIO 3 — parallel materials handoff (float vs pin)"); line()
    # ======================================================================
    mat = api.declare(db, "material", "mat_mo"); api.claim(db, mat, "Captain Face Material", "mat/char/captain", "pat")
    api.bind_source(db, mat, "//depot/mat/captain_face.sbsar", "substance", 5000, "mat_mo")

    anim = api.declare(db, "anim", "anim_lee"); api.claim(db, anim, "Captain Facial Anim", "anim/face/captain", "pat")
    api.relate(db, anim, mat, "DEPENDS_ON", "anim_lee", binding_mode="float")
    r = api.resolve_dependency(db, anim, mat)
    print(f"Animator FLOATS the material -> resolves v{r['resolved_source']['version_num']} ({r['mode']})")

    api.bind_source(db, mat, "//depot/mat/captain_face.sbsar", "substance", 5050, "mat_mo")
    r = api.resolve_dependency(db, anim, mat)
    print(f"Materials publishes v2 -> floating ref now resolves v{r['resolved_source']['version_num']} automatically (no rebuild chain)")

    api.relate(db, anim, mat, "DEPENDS_ON", "anim_lee", binding_mode="pin", pinned_source_version=2)
    api.bind_source(db, mat, "//depot/mat/captain_face.sbsar", "substance", 5099, "mat_mo")
    r = api.resolve_dependency(db, anim, mat)
    print(f"Animator PINS to v2, materials ships v3 -> resolves v{r['resolved_source']['version_num']} ({r['mode']}), stable")

    # ======================================================================
    print("\n\nBONUS — 'staring at it in-editor, where's the source?'"); line()
    # ======================================================================
    api.bind_runtime(db, barrel, "/Game/Junk/Bob/BP_Barrel_FINAL_USETHIS", "build_8821")
    got = api.resolve(db, barrel)
    print(f"Right-click barrel in editor (uuid {barrel[:8]}…):")
    print(f"   identity : {got['identity']['display_name']}")
    print(f"   source   : {got['source']['depot_path']} v{got['source']['version_num']} (CL {got['source']['p4_changelist']})")
    print(f"   runtime  : {got['runtime']['engine_path']}")
    print("   -> 'Open Source' is one lookup. Nobody exports-to-re-edit again.")

    print("\n\nEVENT SPINE (subscribe feed / audit), last 6:"); line()
    for e in db.fetchall("SELECT event_type, actor FROM event ORDER BY id DESC LIMIT 6")[::-1]:
        print(f"   {e['event_type']:22} {e['actor'] or ''}")

    db.close()


if __name__ == "__main__":
    main()
