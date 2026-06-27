"""Live ShotGrid mirror/apply proof (run with a normal python, service up).

Proves the tracker is a faithful VIEW over the IDENTITY facet, both directions,
against a REAL ShotGrid (Flow Production Tracking) site:

  declare + claim in assetcore
    -> adapter.mirror(aid)  -> a real ShotGrid Asset (code + sg_assetcore_uuid)
    -> (simulate production editing the name in ShotGrid)
    -> adapter.apply(aid, ext) -> the edit comes back as a rename of the identity

The adapter does the mirror/apply (the thing under test); a raw shotgun_api3
connection is used only to verify the Asset landed and to simulate the SG-side edit.

Prereqs on the SG site (one-time):
  - a Script (API key): Site Admin -> Scripts -> + Script  (gives a script name + key)
  - two custom text fields on the Asset entity: `sg_assetcore_uuid`, `sg_taxonomy`
    (Asset list view -> + -> Field; type Text). `sg_status_list` exists by default.

Run:
    export SHOTGRID_URL=https://<site>.shotgrid.autodesk.com
    export SHOTGRID_SCRIPT=<your script name>    # exact, case-sensitive (site-specific)
    export SHOTGRID_API_KEY=<the script key>     # secret; never commit
    export SHOTGRID_PROJECT='Demo: Game'         # id or name (SG Assets are project-scoped)
    python -m uvicorn assetcore.service.app:app --port 8765   # (in another shell)
    python scripts/live_shotgrid_mirror.py

Required env: SHOTGRID_URL / SHOTGRID_SCRIPT / SHOTGRID_API_KEY. Optional:
SHOTGRID_PROJECT (default "Demo: Game"), ASSETCORE_URL (default
http://127.0.0.1:8765), ASSETCORE_DEPS (default <repo-parent>/sg-deps).
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEPS = os.environ.get("ASSETCORE_DEPS") or os.path.join(os.path.dirname(_REPO), "sg-deps")
sys.path[:0] = [_REPO, _DEPS]

URL = os.environ.get("ASSETCORE_URL", "http://127.0.0.1:8765")
SG_URL = os.environ.get("SHOTGRID_URL")
SG_SCRIPT = os.environ.get("SHOTGRID_SCRIPT")   # site-specific; no sensible default
SG_KEY = os.environ.get("SHOTGRID_API_KEY")
SG_PROJECT = os.environ.get("SHOTGRID_PROJECT", "Demo: Game")  # id or name; SG Assets are project-scoped
UUID_FIELD = "sg_assetcore_uuid"


def step(msg):
    print(f"[live-sg] {msg}", flush=True)


def main() -> int:
    if not (SG_URL and SG_SCRIPT and SG_KEY):
        print("ERROR: set SHOTGRID_URL, SHOTGRID_SCRIPT, SHOTGRID_API_KEY "
              "(see this file's header).", file=sys.stderr)
        return 2

    import shotgun_api3

    from assetcore.integrations.shotgrid import ShotGridAdapter, _RealShotGridSite
    from assetcore.sdk.client import AssetcoreClient

    sg = shotgun_api3.Shotgun(SG_URL, script_name=SG_SCRIPT, api_key=SG_KEY)  # verify/simulate only
    step(f"connected to {SG_URL} as script {SG_SCRIPT!r}")

    artist = AssetcoreClient(token="artist-token", base_url=URL)   # declare
    prod = AssetcoreClient(token="prod-token", base_url=URL)       # claim/rename (production view)
    try:
        site = _RealShotGridSite(base_url=SG_URL, script_name=SG_SCRIPT, api_key=SG_KEY,
                                 project=SG_PROJECT)
        adapter = ShotGridAdapter(prod, site)
        step(f"target ShotGrid project: {SG_PROJECT!r}")

        name = "Live Test Barrel"
        aid = artist.declare("prop", "env_amy")
        prod.claim(aid, name, "props/containers/barrel", "prod:pat")
        step(f"declared+claimed {aid} as {name!r}")

        step("adapter.mirror -> pushing identity into ShotGrid")
        adapter.mirror(aid)
        rec = sg.find_one("Asset", [[UUID_FIELD, "is", aid]], ["code", "sg_taxonomy"])
        assert rec, "no ShotGrid Asset created for this uuid"
        assert rec["code"] == name, f"SG code mismatch: {rec['code']!r}"
        ext = str(rec["id"])
        step(f"ShotGrid Asset created: id={ext} code={rec['code']!r} "
             f"taxonomy={rec.get('sg_taxonomy')!r}")

        new_name = "Renamed In ShotGrid"
        sg.update("Asset", int(ext), {"code": new_name})   # simulate production editing in SG
        step(f"simulated SG edit: code -> {new_name!r}")

        step("adapter.apply -> pulling the SG edit back as a rename")
        adapter.apply(aid, ext, "prod:pat")
        got = prod.resolve(aid)["identity"]["display_name"]
        assert got == new_name, f"identity not renamed: {got!r}"
        step(f"identity renamed from ShotGrid: resolve() -> {got!r}")

        # cleanup the test Asset so reruns stay clean (best-effort)
        try:
            sg.delete("Asset", int(ext))
            step(f"cleaned up ShotGrid Asset {ext}")
        except Exception as e:   # noqa: BLE001 — cleanup is best-effort
            step(f"(cleanup skipped: {e})")

        print("\n[live-sg] PASS — real ShotGrid mirror/apply round-trip verified.")
        return 0
    finally:
        artist.close()
        prod.close()


if __name__ == "__main__":
    raise SystemExit(main())
