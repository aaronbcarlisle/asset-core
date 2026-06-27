"""CLI contract — drive the agnostic `assetcore` CLI over the real HTTP stack.

run(args, client) is exercised with an injected TestClient-backed AssetcoreClient
(per-authority tokens via make_client), so this covers the CLI dispatch + the SDK
+ the service end to end.
"""
import json

from assetcore.sdk import tools
from assetcore.sdk.cli import build_parser, run


def _args(*argv):
    return build_parser().parse_args(list(argv))


def call(client, *argv, capsys=None):
    """Parse argv, run against the given client, return (exit_code, stdout)."""
    code = run(_args(*argv), client)
    out = capsys.readouterr().out if capsys else ""
    return code, out


def test_global_flags_work_after_subcommand(make_client, capsys):
    # --json/--url/--token must be accepted in either position (before OR after the
    # subcommand) — they live on a shared parent parser.
    artist = make_client("artist-token")
    aid = json.loads(call(artist, "declare", "--type", "prop", "--by", "amy", "--json",
                          capsys=capsys)[1])["id"]
    code, out = call(artist, "resolve", aid, "--json", capsys=capsys)   # flag after the id
    assert code == 0 and json.loads(out)["id"] == aid


def test_declare_resolve_relate_impact(make_client, capsys):
    artist = make_client("artist-token")

    code, out = call(artist, "--json", "declare", "--type", "prop", "--by", "amy", capsys=capsys)
    assert code == 0
    prop = json.loads(out)["id"]

    parent = json.loads(call(artist, "--json", "declare", "--type", "set", "--by", "amy",
                             capsys=capsys)[1])["id"]
    code, _ = call(artist, "relate", parent, prop, "COMPOSED_OF", capsys=capsys)
    assert code == 0

    # impact (alias for dependents) sees the parent
    code, out = call(artist, "--json", "impact", prop, capsys=capsys)
    assert code == 0
    nodes = json.loads(out)
    assert [n["asset_id"] for n in nodes] == [parent] and nodes[0]["depth"] == 1

    # human resolve renders the three-facet block
    code, out = call(artist, "resolve", prop, capsys=capsys)
    assert code == 0 and "identity :" in out and "source   :" in out


def test_claim_rename_deprecate_need_production(make_client, capsys):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    aid = json.loads(call(artist, "--json", "declare", "--type", "prop", "--by", "amy",
                          capsys=capsys)[1])["id"]

    code, _ = call(prod, "claim", aid, "--name", "Barrel", "--taxonomy", "props/x",
                   "--actor", "pat", capsys=capsys)
    assert code == 0
    assert json.loads(call(prod, "--json", "resolve", aid, capsys=capsys)[1])["meta"]["lifecycle"] == "active"

    code, _ = call(prod, "rename", aid, "--name", "Barrel Renamed", "--actor", "pat", capsys=capsys)
    assert code == 0
    assert json.loads(call(prod, "--json", "resolve", aid,
                           capsys=capsys)[1])["identity"]["display_name"] == "Barrel Renamed"

    call(prod, "deprecate", aid, "--actor", "pat", capsys=capsys)
    assert json.loads(call(prod, "--json", "resolve", aid, capsys=capsys)[1])["meta"]["lifecycle"] == "deprecated"


def test_bind_source_then_relocate(make_client, capsys):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    aid = json.loads(call(artist, "--json", "declare", "--type", "prop", "--by", "amy",
                          capsys=capsys)[1])["id"]
    call(artist, "bind-source", aid, "//depot/old/x.ma", "--tool", "maya", "--rev", "1",
         "--by", "amy", capsys=capsys)
    code, _ = call(prod, "relocate", aid, "//depot/new/x.ma", "--actor", "pat", "--rev", "9",
                   capsys=capsys)
    assert code == 0
    src = json.loads(call(artist, "--json", "resolve", aid, capsys=capsys)[1])["source"]
    assert src["location_uri"] == "//depot/new/x.ma" and src["version_num"] == 1


def test_error_path_returns_nonzero(make_client, capsys):
    # an invalid rel_type -> service 400 -> the client raises -> run() propagates;
    # main() would map it to exit 1, but run() itself raises, so assert that.
    artist = make_client("artist-token")
    aid = json.loads(call(artist, "--json", "declare", "--type", "prop", "--by", "amy",
                          capsys=capsys)[1])["id"]
    import httpx
    import pytest
    with pytest.raises(httpx.HTTPStatusError):
        call(artist, "impact", aid, "--rel-types", "BOGUS", capsys=capsys)


# --- production rename/relocate tool (sdk.tools + CLI move/relocate-prefix) ---
def test_impact_report_and_rename_relocate(make_client):
    artist, engine, prod = (make_client("artist-token"), make_client("engine-token"),
                            make_client("prod-token"))
    aid = artist.declare("prop", "amy")
    artist.bind_source(aid, "//depot/old/x.ma", "maya", "1", "amy")
    engine.bind_runtime(aid, "/Game/Old/X", "b1")
    consumer = artist.declare("set", "amy")
    artist.relate(consumer, aid, "COMPOSED_OF")

    rep = tools.impact_report(prod, aid)
    assert rep["total"] == 1 and rep["by_depth"] == {1: 1}

    res = tools.rename_relocate(prod, aid, "pat", new_name="Renamed", new_taxonomy="props/hero",
                                source_uri="//depot/new/x.ma", source_rev="9",
                                runtime_uri="/Game/New/X")
    assert set(res["changed"]) == {"source", "runtime", "identity"}
    r = prod.resolve(aid)
    assert r["identity"]["display_name"] == "Renamed"
    assert r["source"]["location_uri"] == "//depot/new/x.ma" and r["source"]["version_num"] == 1
    assert r["runtime"]["location_uri"] == "/Game/New/X"
    # the relationship survived the rename+relocate
    assert {n["asset_id"] for n in prod.dependents(aid)} == {consumer}


def test_relocate_prefix_directory_move(make_client):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    ids = [artist.declare("prop", "amy") for _ in range(3)]
    for i, aid in enumerate(ids):
        artist.bind_source(aid, f"//depot/art/props/p{i}.ma", "maya", "1", "amy")
    off = artist.declare("prop", "amy")          # different prefix -> should be skipped
    artist.bind_source(off, "//depot/other/o.ma", "maya", "1", "amy")

    res = tools.relocate_prefix(prod, ids + [off], "//depot/art/props/",
                                "//depot/art/env/props/", "pat")
    assert res["moved"] == 3 and res["skipped"] == 1
    assert artist.resolve(ids[0])["source"]["location_uri"] == "//depot/art/env/props/p0.ma"
    assert artist.resolve(off)["source"]["location_uri"] == "//depot/other/o.ma"   # untouched


def test_cli_move_preview_then_apply(make_client, capsys):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    aid = json.loads(call(artist, "--json", "declare", "--type", "prop", "--by", "amy",
                          capsys=capsys)[1])["id"]
    call(artist, "bind-source", aid, "//depot/old.ma", "--tool", "maya", "--rev", "1",
         "--by", "amy", capsys=capsys)

    # preview only (no --yes) -> nothing changes
    code, out = call(prod, "move", aid, "--actor", "pat", "--name", "X",
                     "--source", "//depot/new.ma", capsys=capsys)
    assert code == 0 and "preview only" in out
    assert artist.resolve(aid)["source"]["location_uri"] == "//depot/old.ma"

    # apply
    code, out = call(prod, "move", aid, "--actor", "pat", "--name", "X",
                     "--source", "//depot/new.ma", "--yes", capsys=capsys)
    assert code == 0 and "applied" in out
    r = artist.resolve(aid)
    assert r["source"]["location_uri"] == "//depot/new.ma" and r["identity"]["display_name"] == "X"


def test_cli_relocate_prefix_preview_then_apply(make_client, capsys):
    artist, prod = make_client("artist-token"), make_client("prod-token")
    ids = []
    for i in range(2):
        aid = json.loads(call(artist, "--json", "declare", "--type", "prop", "--by", "amy",
                              capsys=capsys)[1])["id"]
        call(artist, "bind-source", aid, f"//depot/old/p{i}.ma", "--tool", "maya", "--rev", "1",
             "--by", "amy", capsys=capsys)
        ids.append(aid)
    csv = ",".join(ids)

    code, out = call(prod, "relocate-prefix", "//depot/old/", "//depot/new/", "--ids", csv,
                     "--actor", "pat", capsys=capsys)
    assert code == 0 and "preview only" in out
    assert artist.resolve(ids[0])["source"]["location_uri"] == "//depot/old/p0.ma"   # unchanged

    code, out = call(prod, "relocate-prefix", "//depot/old/", "//depot/new/", "--ids", csv,
                     "--actor", "pat", "--yes", capsys=capsys)
    assert code == 0 and "moved 2" in out
    assert artist.resolve(ids[0])["source"]["location_uri"] == "//depot/new/p0.ma"
