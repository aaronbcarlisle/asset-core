"""assetcore CLI — drive the service from a terminal (great for smoke tests).

A thin client over the HTTP API (L2). Commands map one-to-one onto verbs, plus
`subscribe`, which prints the event spine live — so two terminals reproduce the
Phase-3 done-when: one mutates, the other watches events arrive.

    # terminal 1
    uvicorn assetcore.service.app:app
    # terminal 2
    python cli/assetcore_cli.py subscribe
    # terminal 3
    python cli/assetcore_cli.py --token artist-token declare --type prop --by amy
    python cli/assetcore_cli.py resolve --id <uuid>

Config: --url / ASSETCORE_URL (default http://127.0.0.1:8000),
        --token / ASSETCORE_TOKEN (default artist-token).
"""
import argparse
import json
import os
import sys

import httpx

DEFAULT_URL = os.environ.get("ASSETCORE_URL", "http://127.0.0.1:8000")
DEFAULT_TOKEN = os.environ.get("ASSETCORE_TOKEN", "artist-token")


def _headers(args) -> dict:
    return {"X-Assetcore-Token": args.token}


def _print(obj) -> None:
    print(json.dumps(obj, indent=2))


def cmd_declare(args) -> None:
    r = httpx.post(f"{args.url}/assets", headers=_headers(args),
                   json={"asset_type": args.type, "created_by": args.by})
    r.raise_for_status()
    _print(r.json())


def cmd_bind_source(args) -> None:
    r = httpx.post(f"{args.url}/assets/{args.id}/source", headers=_headers(args),
                   json={"location_uri": args.uri, "tool": args.tool,
                         "revision": args.rev, "published_by": args.by})
    r.raise_for_status()
    _print(r.json())


def cmd_relate(args) -> None:
    body = {"from_asset": args.from_, "to_asset": args.to, "rel_type": args.rel,
            "actor": args.by}
    if args.mode:
        body["binding_mode"] = args.mode
    if args.pin is not None:
        body["pinned_version"] = args.pin
    r = httpx.post(f"{args.url}/relate", headers=_headers(args), json=body)
    r.raise_for_status()
    print(f"related {args.from_} -{args.rel}-> {args.to}")


def cmd_resolve(args) -> None:
    r = httpx.get(f"{args.url}/assets/{args.id}", headers=_headers(args))
    r.raise_for_status()
    _print(r.json())


def cmd_subscribe(args) -> None:
    url = f"{args.url}/events"
    params = {"after_seq": args.after}
    print(f"subscribing to {url} (after_seq={args.after}) — Ctrl-C to stop", file=sys.stderr)
    with httpx.stream("GET", url, params=params, headers=_headers(args), timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data:"):
                event = json.loads(line[len("data:"):].strip())
                print(f"[{event['seq']:>4}] {event['event_type']:<20} "
                      f"asset={event['asset_id']} actor={event['actor']} {event['payload']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="assetcore", description=__doc__.splitlines()[0])
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--token", default=DEFAULT_TOKEN)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("declare", help="mint a provisional asset")
    d.add_argument("--type", required=True)
    d.add_argument("--by", required=True)
    d.set_defaults(func=cmd_declare)

    b = sub.add_parser("bind-source", help="publish authored source")
    b.add_argument("--id", required=True)
    b.add_argument("--uri", required=True)
    b.add_argument("--tool", required=True)
    b.add_argument("--rev", required=True)
    b.add_argument("--by", required=True)
    b.set_defaults(func=cmd_bind_source)

    rel = sub.add_parser("relate", help="assert a typed edge")
    rel.add_argument("--from", dest="from_", required=True)
    rel.add_argument("--to", required=True)
    rel.add_argument("--rel", required=True)
    rel.add_argument("--mode", choices=["float", "pin"])
    rel.add_argument("--pin", type=int)
    rel.add_argument("--by", required=True)
    rel.set_defaults(func=cmd_relate)

    rs = sub.add_parser("resolve", help="UUID -> all three facets")
    rs.add_argument("--id", required=True)
    rs.set_defaults(func=cmd_resolve)

    su = sub.add_parser("subscribe", help="stream the event spine live")
    su.add_argument("--after", type=int, default=0)
    su.set_defaults(func=cmd_subscribe)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except httpx.HTTPStatusError as exc:
        print(f"error {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 1
    except httpx.ConnectError:
        print(f"could not connect to {args.url} — is the service running?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
