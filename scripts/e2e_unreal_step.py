"""E2E step 2 (run inside the UE editor): cook the barrel into Unreal, bind RUNTIME.

Reads the ONE identity minted by the Maya step (ASSETCORE_E2E_AID) and carries it
forward: ensure a /Game asset for the barrel, stamp THAT SAME id onto it (the
"import preserves identity" step — never mint a second one), then bind the runtime
facet. Proves source (Maya/P4) and runtime (Unreal) hang off a single UUID.

Launched headless by scripts/live_e2e.py via UnrealEditor-Cmd -ExecutePythonScript.
Result -> %TEMP%/assetcore_e2e_unreal_result.txt (3dsmaxbatch/UE don't surface stdout).
"""
import os
import sys
import tempfile
import traceback

_REPO = os.environ.get("ASSETCORE_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEPS = os.environ.get("ASSETCORE_DEPS") or os.path.join(os.path.dirname(_REPO), "ue-deps")
sys.path[:0] = [_REPO, _DEPS]

URL = os.environ.get("ASSETCORE_URL", "http://127.0.0.1:8765")
AID = os.environ.get("ASSETCORE_E2E_AID")
ASSET = "/Game/AssetcoreProbe/M_e2e_barrel"
RESULT = os.path.join(tempfile.gettempdir(), "assetcore_e2e_unreal_result.txt")


def write(msg):
    with open(RESULT, "w", encoding="utf-8") as f:
        f.write(msg)


def main():
    import unreal

    from assetcore.integrations.unreal import UnrealAdapter
    from assetcore.sdk.client import AssetcoreClient

    if not AID:
        write("FAIL: ASSETCORE_E2E_AID not set")
        return 1

    eal = unreal.EditorAssetLibrary
    # fresh asset each run so the stamp is clean (write_stamp guards overwrites)
    if eal.does_asset_exist(ASSET):
        eal.delete_asset(ASSET)
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    tools.create_asset("M_e2e_barrel", "/Game/AssetcoreProbe", unreal.Material,
                       unreal.MaterialFactoryNew())
    eal.save_asset(ASSET)

    with AssetcoreClient(token="engine-token", base_url=URL) as client:
        adapter = UnrealAdapter(client)               # real unreal editor seam
        adapter.write_stamp(ASSET, AID)               # carry the Maya identity forward
        assert adapter.read_stamp(ASSET) == AID, "stamp did not round-trip"
        ver = client.bind_runtime(AID, adapter.current_location(ASSET), "ci_build_e2e")
        write(f"PASS aid={AID} runtime={adapter.current_location(ASSET)} version={ver}")
    return 0


try:
    main()
except Exception:
    write("FAIL\n" + traceback.format_exc())
    sys.exit(1)
