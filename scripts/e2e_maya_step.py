"""E2E step 1 (run under mayapy): author the barrel in Maya, bind its SOURCE facet.

Mints the ONE identity for the barrel (publish: stamp the .ma + bind_source to the
live P4 depot path) and writes the asset id to a handoff file so the orchestrator
can carry the SAME identity into Unreal. Part of scripts/live_e2e.py.
"""
import os
import sys
import tempfile
import traceback

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULTS = {
    "ASSETCORE_URL": "http://127.0.0.1:8765",
    "ASSETCORE_DEPS": os.path.join(os.path.dirname(_REPO), "maya-deps"),
    "P4_BIN": os.path.join(os.path.dirname(_REPO), "p4local", "bin"),
    "P4_WS": os.path.join(os.path.dirname(_REPO), "p4local", "ws"),
    "P4PORT": "localhost:1666", "P4USER": "assetcore", "P4CLIENT": "assetcore_ws",
}
for k, v in _DEFAULTS.items():
    os.environ.setdefault(k, v)
sys.path[:0] = [_REPO, os.environ["ASSETCORE_DEPS"]]
os.environ["PATH"] = os.environ["P4_BIN"] + os.pathsep + os.environ.get("PATH", "")

DOC = os.path.join(os.environ["P4_WS"], "art", "props", "e2e_barrel.ma")
AID_FILE = os.path.join(tempfile.gettempdir(), "assetcore_e2e_aid.txt")
RESULT = os.path.join(tempfile.gettempdir(), "assetcore_e2e_maya_result.txt")


def main():
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        from assetcore.integrations.maya import MayaAdapter
        from assetcore.sdk.client import AssetcoreClient

        os.makedirs(os.path.dirname(DOC), exist_ok=True)
        with AssetcoreClient(token="artist-token", base_url=os.environ["ASSETCORE_URL"]) as client:
            adapter = MayaAdapter(client)             # real maya.cmds + real p4 seams
            aid = adapter.publish(DOC, "prop", "env_amy")
            assert adapter.read_stamp(DOC) == aid     # stamp round-trips from the .ma
            loc = client.resolve(aid)["source"]["location_uri"]
            with open(AID_FILE, "w", encoding="utf-8") as f:
                f.write(aid)
            with open(RESULT, "w", encoding="utf-8") as f:
                f.write(f"PASS aid={aid} source={loc}")
    finally:
        maya.standalone.uninitialize()


try:
    main()
except Exception:
    with open(RESULT, "w", encoding="utf-8") as f:
        f.write("FAIL\n" + traceback.format_exc())
    sys.exit(1)
