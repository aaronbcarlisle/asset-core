"""CLI contract — drive the agnostic `assetcore` CLI over the real HTTP stack.

run(args, client) is exercised with an injected TestClient-backed AssetcoreClient
(per-authority tokens via make_client), so this covers the CLI dispatch + the SDK
+ the service end to end.
"""
import json

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
