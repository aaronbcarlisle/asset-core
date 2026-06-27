"""Live Unreal -> assetcore reconcile (run INSIDE the editor, not pytest).

Unlike Maya, the `unreal` module exists only inside a running editor, so this is
launched headless via UnrealEditor-Cmd.exe (see the run line below). It drives a
REAL reconcile against a running assetcore service: ensure a probe asset exists in
/Game, mint+stamp identity into each asset's metadata tag, then reconcile -> bind
the runtime facet for every stamped asset. The on-iron confirmation of the Phase-5
engine side.

Run (with the service up — see docs/LIVE_PROVING.md):
    UE=/d/UE/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe
    "$UE" D:/FFXIV/Dev/asset-management/ue_proj/AssetcoreProbe.uproject \
        -nullrhi -unattended -nosplash -nop4 \
        -ExecutePythonScript="D:/FFXIV/Dev/asset-management/assetcore/scripts/live_unreal_reconcile.py"

Config via env (local defaults):
    ASSETCORE_URL   service base url   (default http://127.0.0.1:8765)
    ASSETCORE_REPO  assetcore repo dir (default: this file's repo)
    ASSETCORE_DEPS  httpx for UE py    (default: <repo-parent>/ue-deps)

Look for the line "LIVE_UNREAL_RESULT:" in the (verbose) editor log for the outcome.
"""
import os
import sys
import traceback

_REPO = os.environ.get("ASSETCORE_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEPS = os.environ.get("ASSETCORE_DEPS") or os.path.join(os.path.dirname(_REPO), "ue-deps")
sys.path[:0] = [_REPO, _DEPS]

URL = os.environ.get("ASSETCORE_URL", "http://127.0.0.1:8765")
PROBE_DIR = "/Game/AssetcoreProbe"


def log(msg):
    print(f"[live-unreal] {msg}", flush=True)


def _ensure_probe_asset():
    """Create a tiny Material in /Game if the project has no assets yet, so the
    reconcile walk has something to bind. Returns the list of /Game asset paths."""
    import unreal

    eal = unreal.EditorAssetLibrary
    existing = list(eal.list_assets("/Game", recursive=True, include_folder=False))
    if existing:
        log(f"/Game already has {len(existing)} asset(s); using them")
        return existing

    log("/Game is empty — creating a probe Material")
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    tools.create_asset("M_assetcore_probe", PROBE_DIR, unreal.Material,
                       unreal.MaterialFactoryNew())
    eal.save_directory("/Game", only_if_is_dirty=False)
    return list(eal.list_assets("/Game", recursive=True, include_folder=False))


def main():
    from assetcore.integrations.unreal import UnrealAdapter
    from assetcore.sdk.client import AssetcoreClient

    paths = _ensure_probe_asset()
    log(f"reconcile candidates: {paths}")

    with AssetcoreClient(token="engine-token", base_url=URL) as client:
        adapter = UnrealAdapter(client)   # REAL unreal editor seam

        for p in paths:
            aid = adapter.ensure_identity(p, "engine_asset")
            log(f"identity for {p}: {aid} (stamped: {adapter.read_stamp(p)})")

        bound = adapter.reconcile("ci_build_001")
        log(f"reconcile bound {len(bound)} asset(s): {bound}")
        assert bound, "reconcile bound nothing"
        assert all(v >= 1 for v in bound.values()), "a runtime version was < 1"

        # verify the runtime facet round-trips through resolve() for one asset
        sample = next(iter(bound))
        aid = adapter.read_stamp(sample)
        facets = client.resolve(aid)
        rt = facets["runtime"]["location_uri"]
        assert rt == adapter.current_location(sample), f"runtime loc mismatch: {rt!r}"
        log(f"runtime facet round-trips via resolve(): {rt}")

    print("LIVE_UNREAL_RESULT: PASS", flush=True)


try:
    main()
except Exception:
    traceback.print_exc()
    print("LIVE_UNREAL_RESULT: FAIL", flush=True)
    # exit non-zero so CI/automation sees the failure (the editor propagates a failed
    # script run) instead of a misleading clean exit after a printed FAIL.
    sys.exit(1)
