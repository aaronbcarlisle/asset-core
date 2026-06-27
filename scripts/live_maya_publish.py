"""Live Maya -> Perforce proof (run under mayapy, NOT pytest).

Drives a REAL Maya publish through the REAL p4 seams against a running assetcore
service: mint identity -> stamp into the .ma fileInfo -> bind the source facet to
the live depot path -> verify the stamp round-trips and resolve() reflects it.
Then submits to P4 and re-publishes to show the source version advance to a real CL.

This is the on-iron confirmation of the Phase-5 "barrel through real Maya -> P4"
done-when. The adapter logic is already proven against fakes in tests/contract; this
exercises _RealMayaScene (maya.cmds) + _RealMayaVcs (the p4 CLI) for real.

Run:
    # 1) start the service in a normal python (has fastapi):
    python -m uvicorn assetcore.service.app:app --port 8765
    # 2) drive Maya:
    "/c/Program Files/Autodesk/Maya2027/bin/mayapy.exe" scripts/live_maya_publish.py

Config via env (sensible local defaults):
    ASSETCORE_URL   service base url        (default http://127.0.0.1:8765)
    ASSETCORE_DEPS  extra sys.path dir      (httpx for mayapy; default maya-deps)
    P4_BIN          dir holding p4.exe      (default the local p4local/bin)
    P4_WS           workspace root          (default the local p4local/ws)
    P4PORT/P4USER/P4CLIENT  p4 connection   (defaults: localhost:1666/assetcore/assetcore_ws)
"""
import os
import subprocess
import sys

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

# make assetcore + httpx importable, and p4.exe findable by the real vcs seam
sys.path[:0] = [_REPO, os.environ["ASSETCORE_DEPS"]]
os.environ["PATH"] = os.environ["P4_BIN"] + os.pathsep + os.environ.get("PATH", "")

P4 = os.path.join(os.environ["P4_BIN"], "p4.exe")
WS = os.environ["P4_WS"]
DOC = os.path.join(WS, "art", "props", "live_barrel.ma")


def p4(*args):
    return subprocess.run([P4, *args], capture_output=True, text=True, check=True).stdout.strip()


def step(msg):
    print(f"[live-maya] {msg}", flush=True)


def main() -> int:
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        from assetcore.integrations.maya import MayaAdapter
        from assetcore.sdk.client import AssetcoreClient

        os.makedirs(os.path.dirname(DOC), exist_ok=True)
        with AssetcoreClient(token="artist-token", base_url=os.environ["ASSETCORE_URL"]) as client:
            adapter = MayaAdapter(client)   # REAL maya.cmds + REAL p4 seams

            step(f"publishing {DOC} (first publish: mint + stamp + bind_source)")
            aid = adapter.publish(DOC, "prop", "env_amy")
            step(f"minted identity: {aid}")

            stamped = adapter.read_stamp(DOC)
            assert stamped == aid, f"stamp round-trip failed: {stamped!r} != {aid!r}"
            step(f"stamp round-trips from the .ma fileInfo: {stamped}")

            facets = client.resolve(aid)
            loc = facets["source"]["location_uri"]
            assert loc == adapter.current_location(DOC), f"source loc mismatch: {loc!r}"
            step(f"source facet bound to live depot path: {loc} "
                 f"(rev {facets['source']['revision']})")

            step("submitting the .ma to Perforce, then re-publishing to advance the version")
            try:
                p4("add", DOC)
            except subprocess.CalledProcessError:
                p4("edit", DOC)            # already added on a prior run
            p4("submit", "-d", "assetcore live maya publish", DOC)
            cl = p4("-ztag", "-F", "%change%", "changes", "-m1", DOC)
            v2 = client.bind_source(aid, adapter.current_location(DOC), "maya",
                                    adapter.current_revision(DOC), "env_amy")
            step(f"re-published: source version {v2} now at real changelist {cl}")
            assert adapter.current_revision(DOC) == cl, "revision seam disagrees with p4"

            print("\n[live-maya] PASS — real Maya -> P4 -> assetcore round-trip verified.")
            return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
