"""Phase-16 endpoints over the real HTTP stack (via the SDK client + TestClient):
transitive dependents/dependencies, relocate, deprecate, stale-derivations, bulk.
"""


def test_dependents_dependencies_over_http(make_client):
    c = make_client("artist-token")
    city = c.declare("set", "amy")
    district = c.declare("set", "amy")
    prop = c.declare("prop", "amy")
    c.relate(city, district, "COMPOSED_OF")
    c.relate(district, prop, "COMPOSED_OF")

    dep = {d["asset_id"]: d["depth"] for d in c.dependents(prop)}
    assert dep == {district: 1, city: 2}
    deps = {d["asset_id"]: d["depth"] for d in c.dependencies(city)}
    assert deps == {district: 1, prop: 2}
    # rel_type filter + depth bound flow through the query params
    assert c.dependents(prop, rel_types=["DEPENDS_ON"]) == []
    assert {d["asset_id"] for d in c.dependents(prop, depth=1)} == {district}


def test_relocate_over_http(make_client):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    a = artist.declare("prop", "amy")
    artist.bind_source(a, "//depot/old/barrel.ma", "maya", "10", "amy")
    prod.claim(a, "Barrel", "props/barrel", "pat")

    prod.relocate(a, "//depot/new/env/barrel.ma", "pat", new_revision="42")
    facets = prod.resolve(a)
    assert facets["source"]["location_uri"] == "//depot/new/env/barrel.ma"
    assert facets["source"]["revision"] == "42"
    assert facets["source"]["version_num"] == 1          # a move, not a new version
    assert facets["identity"]["display_name"] == "Barrel"  # identity untouched


def test_deprecate_over_http(make_client):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    a = artist.declare("prop", "amy")
    prod.claim(a, "Old", "props/x", "pat")
    prod.deprecate(a, "pat")
    assert prod.resolve(a)["meta"]["lifecycle"] == "deprecated"


def test_stale_derivations_over_http(make_client):
    c = make_client("artist-token")
    hi = c.declare("sculpt", "amy")
    low = c.declare("mesh", "amy")
    c.bind_source(hi, "//depot/hi.ztl", "zbrush", "1", "amy")
    c.relate(low, hi, "DERIVED_FROM")
    assert c.stale_derivations(low) == []
    c.bind_source(hi, "//depot/hi.ztl", "zbrush", "2", "amy")   # re-sculpt
    stale = c.stale_derivations(low)
    assert len(stale) == 1 and stale[0]["to_asset"] == hi


def test_bulk_over_http(make_client):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    ids = artist.bulk_declare([{"asset_type": "prop", "created_by": "amy"} for _ in range(4)])
    assert len(ids) == 4
    env = artist.declare("set", "amy")
    n = artist.bulk_relate([{"from_asset": env, "to_asset": pid, "rel_type": "COMPOSED_OF"}
                            for pid in ids])
    assert n == 4 and len({d["asset_id"] for d in artist.dependencies(env)}) == 4

    for pid in ids:
        artist.bind_source(pid, f"//depot/old/{pid}.ma", "maya", "1", "amy")
    moves = [{"asset_id": pid, "new_location_uri": f"//depot/new/{pid}.ma", "actor": "pat"}
             for pid in ids]
    assert prod.bulk_relocate(moves) == 4
    assert artist.resolve(ids[0])["source"]["location_uri"].startswith("//depot/new/")
