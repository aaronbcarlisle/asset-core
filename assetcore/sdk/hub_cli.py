"""UGS hub CLI (L3). Firewall: imports assetcore.sdk.* only (spec §7.4).

Invoked as `assetcore hub <verb>` via sdk.cli.main. Lives in the SDK (not L4)
because it speaks only to the SDK — no integration reaches into it."""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from assetcore.sdk.hub import (
    health_check, launch_dcc, load_hub_context,
    load_pipeline_config, make_builtins, resolve_target,
)

_DEFAULT_TOKEN = os.environ.get("ASSETCORE_TOKEN", "artist-token")


def _client(central_url: str):
    from assetcore.sdk.client import AssetcoreClient
    return AssetcoreClient(token=_DEFAULT_TOKEN, base_url=central_url)


def _emit(obj) -> None:
    print(json.dumps(obj))


def _cmd_launch(args) -> int:
    ctx = load_hub_context(args.context)
    pipeline = load_pipeline_config(ctx["pipeline_config_path"])
    target = next((t for t in pipeline.launch_targets if t.id == args.tool_id), None)
    if target is None:
        _emit({"ok": False, "pid": None, "error": f"unknown tool_id: {args.tool_id}"})
        return 1
    result = launch_dcc(target, pipeline, ctx)
    _emit(result)
    return 0 if result["ok"] else 1


def _cmd_hydrate(args) -> int:
    from assetcore.sdk.replica import hydrate_cache
    ctx = load_hub_context(args.context)
    pipeline = load_pipeline_config(ctx["pipeline_config_path"])
    result = hydrate_cache(pipeline, ctx, _client(pipeline.central_url),
                           now_iso=datetime.now(timezone.utc).isoformat())
    _emit(result)
    return 0


def _cmd_serve_local(args) -> int:
    from assetcore.sdk.local_reader import serve_local
    serve_local(load_pipeline_config(args.config))
    return 0


def _cmd_sync_outbox(args) -> int:
    from assetcore.sdk.offline import open_outbox, replay_outbox
    pipeline = load_pipeline_config(args.config)
    conn = open_outbox(pipeline.outbox_path)
    result = replay_outbox(conn, _client(pipeline.central_url))
    conn.close()
    _emit(result)
    return 0


def _cmd_retry_failed(args) -> int:
    from assetcore.sdk.offline import open_outbox, replay_outbox, retry_failed
    pipeline = load_pipeline_config(args.config)
    conn = open_outbox(pipeline.outbox_path)
    reset = retry_failed(conn)
    result = replay_outbox(conn, _client(pipeline.central_url))
    conn.close()
    _emit({"reset": reset, **result})
    return 0


def _cmd_health(args) -> int:
    _emit(health_check(load_pipeline_config(args.config)))
    return 0


def _cmd_list_targets(args) -> int:
    pipeline = load_pipeline_config(args.config)
    builtins = make_builtins(pipeline.config_path)
    out = []
    for t in pipeline.launch_targets:
        exe, reason = resolve_target(t, os.environ, builtins)
        out.append({"id": t.id, "label": t.label, "icon": t.icon,
                    "available": exe is not None, "reason": reason,
                    "executable_path": exe})
    _emit(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assetcore")
    sub = parser.add_subparsers(dest="group", required=True)
    hub = sub.add_parser("hub").add_subparsers(dest="verb", required=True)

    p = hub.add_parser("launch"); p.add_argument("tool_id"); p.add_argument("--context", required=True); p.set_defaults(fn=_cmd_launch)
    p = hub.add_parser("hydrate"); p.add_argument("--context", required=True); p.set_defaults(fn=_cmd_hydrate)
    p = hub.add_parser("serve-local"); p.add_argument("--config", required=True); p.set_defaults(fn=_cmd_serve_local)
    p = hub.add_parser("sync-outbox"); p.add_argument("--config", required=True); p.set_defaults(fn=_cmd_sync_outbox)
    p = hub.add_parser("retry-failed"); p.add_argument("--config", required=True); p.set_defaults(fn=_cmd_retry_failed)
    p = hub.add_parser("health"); p.add_argument("--config", required=True); p.set_defaults(fn=_cmd_health)
    p = hub.add_parser("list-targets"); p.add_argument("--config", required=True); p.set_defaults(fn=_cmd_list_targets)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:
        _emit({"ok": False, "error": repr(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
