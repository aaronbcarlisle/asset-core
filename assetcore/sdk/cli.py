"""assetcore — the agnostic command-line interface (L3).

One subcommand per universal verb, over the same `AssetcoreClient` (HTTP) every
integration uses — so artists, production, developers, and pipeline automation all
drive the system through one surface. Pure stdlib argparse + the SDK client; it
imports nothing below the HTTP boundary (the SDK firewall covers this module).

    assetcore resolve <id>
    assetcore impact <id> --rel-types COMPOSED_OF,DEPENDS_ON --depth 3
    assetcore relocate <id> //depot/new/path.ma --actor pat --rev 42
    assetcore deprecate <id> --actor pat

Config: --url / $ASSETCORE_URL (default http://127.0.0.1:8000) and
--token / $ASSETCORE_TOKEN (default artist-token, dev-grade). `--json` on any
command prints the raw service JSON for scripting.

`run(args, client)` holds the dispatch (testable with an injected client);
`main()` builds the client from flags/env and calls it.
"""
from __future__ import annotations

import argparse
import json as _json
import os
import sys

from assetcore.sdk.client import AssetcoreClient

DEFAULT_URL = os.environ.get("ASSETCORE_URL", "http://127.0.0.1:8000")
DEFAULT_TOKEN = os.environ.get("ASSETCORE_TOKEN", "artist-token")


def _out(args, data, human: str) -> None:
    print(_json.dumps(data, indent=2, default=str) if getattr(args, "json", False) else human)


def _node_line(n: dict) -> str:
    return f"  [{n['depth']}] {n['rel_type']:<12} {n['asset_id']}"


def _rel_line(r: dict) -> str:
    pin = f" pin@{r['pinned_version']}" if r.get("pinned_version") else ""
    mode = f" ({r['binding_mode']}{pin})" if r.get("binding_mode") else ""
    return f"  {r['from_asset']} -{r['rel_type']}-> {r['to_asset']}{mode}"


def build_parser() -> argparse.ArgumentParser:
    # global flags live on a shared parent applied to the root AND every subparser,
    # so `--url/--token/--json` work either before OR after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    # default=SUPPRESS so a flag given before the subcommand isn't clobbered by the
    # subparser's default (the argparse parents gotcha); effective values via getattr.
    common.add_argument("--url", default=argparse.SUPPRESS, help="service base url")
    common.add_argument("--token", default=argparse.SUPPRESS, help="auth token (maps to an authority)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print raw service JSON")

    p = argparse.ArgumentParser(prog="assetcore", parents=[common],
                                description="identity-first asset management")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    g = add("resolve", help="show an asset's three facets")
    g.add_argument("asset_id")

    g = add("declare", help="mint a provisional asset")
    g.add_argument("--type", required=True, dest="asset_type")
    g.add_argument("--by", required=True, dest="created_by")

    g = add("claim", help="give a provisional asset identity (production)")
    g.add_argument("asset_id"); g.add_argument("--name", required=True)
    g.add_argument("--taxonomy", required=True); g.add_argument("--actor", required=True)

    g = add("rename", help="relabel the identity facet only (production)")
    g.add_argument("asset_id"); g.add_argument("--name", required=True)
    g.add_argument("--actor", required=True); g.add_argument("--taxonomy", default=None)

    g = add("bind-source", help="publish the source facet (artist)")
    g.add_argument("asset_id"); g.add_argument("location_uri")
    g.add_argument("--tool", required=True); g.add_argument("--rev", required=True, dest="revision")
    g.add_argument("--by", required=True, dest="published_by")

    g = add("bind-runtime", help="report the runtime facet (engine/build)")
    g.add_argument("asset_id"); g.add_argument("location_uri"); g.add_argument("--build", required=True)

    g = add("relate", help="assert a typed edge")
    g.add_argument("from_asset"); g.add_argument("to_asset"); g.add_argument("rel_type")
    g.add_argument("--mode", choices=["float", "pin"], default=None)
    g.add_argument("--pin", type=int, default=None, dest="pinned_version")
    g.add_argument("--actor", default=None)

    g = add("relocate", help="move the bytes in place (same identity/version)")
    g.add_argument("asset_id"); g.add_argument("location_uri"); g.add_argument("--actor", required=True)
    g.add_argument("--facet", choices=["source", "runtime"], default="source")
    g.add_argument("--rev", default=None, dest="new_revision")

    g = add("deprecate", help="retire an identity (production)")
    g.add_argument("asset_id"); g.add_argument("--actor", required=True)

    for name, helptext in [("dependents", "what (transitively) depends on this asset"),
                           ("dependencies", "what this asset is built from"),
                           ("impact", "alias for dependents (the 'what breaks' view)")]:
        g = add(name, help=helptext)
        g.add_argument("asset_id")
        g.add_argument("--rel-types", default=None, dest="rel_types")
        g.add_argument("--depth", type=int, default=None)

    add("used-by", help="direct consumers (one hop)").add_argument("asset_id")
    add("lineage", help="what this derives from / instances").add_argument("asset_id")
    add("stale-derivations", help="DERIVED_FROM edges whose source advanced").add_argument("asset_id")
    add("floating", help="DEPENDS_ON edges still floating (pin before ship)").add_argument("asset_id")

    g = add("find-similar", help="reuse-over-rebuild nudge (advisory)")
    g.add_argument("name"); g.add_argument("--type", default=None, dest="asset_type")

    add("worklist", help="provisional backfill queue (oldest first)")
    return p


def run(args, client: AssetcoreClient) -> int:
    cmd = args.command
    if cmd == "resolve":
        r = client.resolve(args.asset_id)
        ident, src, rt = r.get("identity") or {}, r.get("source") or {}, r.get("runtime") or {}
        meta = r.get("meta") or {}
        human = (f"{args.asset_id}\n"
                 f"  identity : {ident.get('display_name')!r}  "
                 f"({meta.get('lifecycle')}, {meta.get('asset_type')})\n"
                 f"  source   : {src.get('tool')} {src.get('location_uri')} "
                 f"(rev {src.get('revision')}, v{src.get('version_num')})\n"
                 f"  runtime  : {rt.get('location_uri')} (build {rt.get('build_id')})")
        _out(args, r, human)
    elif cmd == "declare":
        aid = client.declare(args.asset_type, args.created_by)
        _out(args, {"id": aid}, aid)
    elif cmd == "claim":
        client.claim(args.asset_id, args.name, args.taxonomy, args.actor)
        _out(args, {"ok": True}, f"claimed {args.asset_id} as {args.name!r}")
    elif cmd == "rename":
        client.rename(args.asset_id, args.name, args.actor, args.taxonomy)
        _out(args, {"ok": True}, f"renamed {args.asset_id} -> {args.name!r}")
    elif cmd == "bind-source":
        v = client.bind_source(args.asset_id, args.location_uri, args.tool, args.revision, args.published_by)
        _out(args, {"version": v}, f"source v{v} -> {args.location_uri}")
    elif cmd == "bind-runtime":
        v = client.bind_runtime(args.asset_id, args.location_uri, args.build)
        _out(args, {"version": v}, f"runtime v{v} -> {args.location_uri}")
    elif cmd == "relate":
        client.relate(args.from_asset, args.to_asset, args.rel_type,
                      binding_mode=args.mode, pinned_version=args.pinned_version, actor=args.actor)
        _out(args, {"ok": True}, f"{args.from_asset} -{args.rel_type}-> {args.to_asset}")
    elif cmd == "relocate":
        client.relocate(args.asset_id, args.location_uri, args.actor, args.facet, args.new_revision)
        _out(args, {"ok": True}, f"relocated {args.facet} of {args.asset_id} -> {args.location_uri}")
    elif cmd == "deprecate":
        client.deprecate(args.asset_id, args.actor)
        _out(args, {"ok": True}, f"deprecated {args.asset_id}")
    elif cmd in ("dependents", "impact"):
        rel = args.rel_types.split(",") if args.rel_types else None
        nodes = client.dependents(args.asset_id, rel, args.depth)
        _out(args, nodes, f"{len(nodes)} dependents:\n" + "\n".join(_node_line(n) for n in nodes))
    elif cmd == "dependencies":
        rel = args.rel_types.split(",") if args.rel_types else None
        nodes = client.dependencies(args.asset_id, rel, args.depth)
        _out(args, nodes, f"{len(nodes)} dependencies:\n" + "\n".join(_node_line(n) for n in nodes))
    elif cmd == "used-by":
        rels = client.used_by(args.asset_id)
        _out(args, rels, "\n".join(_rel_line(r) for r in rels) or "  (none)")
    elif cmd == "lineage":
        rels = client.lineage(args.asset_id)
        _out(args, rels, "\n".join(_rel_line(r) for r in rels) or "  (none)")
    elif cmd == "stale-derivations":
        rels = client.stale_derivations(args.asset_id)
        _out(args, rels, f"{len(rels)} stale:\n" + "\n".join(_rel_line(r) for r in rels))
    elif cmd == "floating":
        rels = client.floating_dependencies(args.asset_id)
        _out(args, rels, f"{len(rels)} floating:\n" + "\n".join(_rel_line(r) for r in rels))
    elif cmd == "find-similar":
        hits = client.find_similar(args.name, args.asset_type)
        _out(args, hits, "\n".join(f"  {h['score']:>2}  {h['display_name']!r}  {h['id']}"
                                   for h in hits) or "  (no similar assets)")
    elif cmd == "worklist":
        items = client.backfill_worklist()
        _out(args, items, f"{len(items)} provisional:\n" +
             "\n".join(f"  {i['asset_type']:<10} {i['id']}  ({i['created_by']})" for i in items))
    else:   # pragma: no cover - argparse 'required' prevents this
        return 2
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    url = getattr(args, "url", DEFAULT_URL)
    token = getattr(args, "token", DEFAULT_TOKEN)
    try:
        with AssetcoreClient(token=token, base_url=url) as client:
            return run(args, client)
    except Exception as exc:   # noqa: BLE001 — CLI maps any failure to a clean message + exit 1
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
