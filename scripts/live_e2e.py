"""Cross-tool E2E (run with a normal python, service up): ONE barrel, ONE identity,
three sovereign facets each written by its own authority and tool.

    artist  : Maya publish    -> SOURCE facet  (//depot/.../e2e_barrel.ma in Perforce)
    prod    : claim           -> IDENTITY facet (display name + taxonomy)
    engine  : Unreal reconcile-> RUNTIME facet  (/Game/.../M_e2e_barrel)
    => resolve(aid) shows all three on the SAME UUID — identity is not the path.

It orchestrates two real tool runtimes (each in its own process): mayapy for the
Maya step, UnrealEditor-Cmd for the Unreal step, threading the minted id between
them. The headline of the live-proving work, end to end.

Run (see docs/LIVE_PROVING.md for the per-tool deps):
    python -m uvicorn assetcore.service.app:app --port 8765   # (another shell)
    python scripts/live_e2e.py

Config via env (local defaults): ASSETCORE_URL, MAYAPY, UE_CMD, UE_PROJECT,
MAYA_DEPS / UE_DEPS (httpx for each runtime).
"""
import os
import subprocess
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_REPO)
URL = os.environ.get("ASSETCORE_URL", "http://127.0.0.1:8765")
MAYAPY = os.environ.get("MAYAPY", r"C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe")
UE_CMD = os.environ.get("UE_CMD", r"D:\UE\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe")
UE_PROJECT = os.environ.get("UE_PROJECT",
                            os.path.join(_PARENT, "ue_proj", "AssetcoreProbe.uproject"))
MAYA_DEPS = os.environ.get("MAYA_DEPS", os.path.join(_PARENT, "maya-deps"))
UE_DEPS = os.environ.get("UE_DEPS", os.path.join(_PARENT, "ue-deps"))

_TMP = tempfile.gettempdir()
AID_FILE = os.path.join(_TMP, "assetcore_e2e_aid.txt")
MAYA_RESULT = os.path.join(_TMP, "assetcore_e2e_maya_result.txt")
UE_RESULT = os.path.join(_TMP, "assetcore_e2e_unreal_result.txt")

sys.path.insert(0, _REPO)


def banner(msg):
    print(f"\n=== {msg} ===", flush=True)


def read(path):
    return open(path, encoding="utf-8").read().strip() if os.path.exists(path) else ""


def run_step(name, argv, env_extra, result_path, timeout):
    for p in (result_path,):
        if os.path.exists(p):
            os.remove(p)
    env = {**os.environ, **env_extra}
    proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    res = read(result_path)
    if proc.returncode != 0 or not res.startswith("PASS"):
        print(f"[e2e] {name} FAILED (rc={proc.returncode}):\n{res or proc.stderr[-800:]}")
        raise SystemExit(1)
    print(f"[e2e] {name}: {res}")
    return res


def main():
    from assetcore.sdk.client import AssetcoreClient

    banner("step 1/3 — Maya authors the barrel (SOURCE facet, via mayapy + Perforce)")
    run_step("maya", [MAYAPY, os.path.join(_REPO, "scripts", "e2e_maya_step.py")],
             {"ASSETCORE_URL": URL, "ASSETCORE_DEPS": MAYA_DEPS}, MAYA_RESULT, timeout=300)
    aid = read(AID_FILE)
    assert aid, "Maya step did not emit an asset id"
    print(f"[e2e] one identity minted: {aid}")

    banner("step 2/3 — Production claims it (IDENTITY facet)")
    with AssetcoreClient(token="prod-token", base_url=URL) as prod:
        prod.claim(aid, "Weathered Barrel", "props/containers/barrel", "prod:pat")
    print("[e2e] claimed as 'Weathered Barrel'")

    banner("step 3/3 — Unreal cooks it (RUNTIME facet, via UnrealEditor-Cmd)")
    run_step("unreal",
             [UE_CMD, UE_PROJECT, "-nullrhi", "-unattended", "-nosplash", "-nop4",
              f"-ExecutePythonScript={os.path.join(_REPO, 'scripts', 'e2e_unreal_step.py')}"],
             {"ASSETCORE_URL": URL, "ASSETCORE_DEPS": UE_DEPS, "ASSETCORE_REPO": _REPO,
              "ASSETCORE_E2E_AID": aid}, UE_RESULT, timeout=600)

    banner("resolve(aid) — three sovereign facets on ONE identity")
    with AssetcoreClient(token="artist-token", base_url=URL) as client:
        facets = client.resolve(aid)
    ident, src, rt, meta = (facets["identity"], facets["source"],
                            facets["runtime"], facets["meta"])
    print(f"  identity : {ident['display_name']!r}  ({meta['lifecycle']})  uuid={aid}")
    print(f"  source   : {src['tool']}  {src['location_uri']}  (rev {src['revision']})")
    print(f"  runtime  : {rt['location_uri']}  (build {rt['build_id']})")

    assert ident["display_name"] == "Weathered Barrel"
    assert src["location_uri"].endswith("e2e_barrel.ma") and src["tool"] == "maya"
    assert rt["location_uri"].endswith("M_e2e_barrel")
    print("\n[e2e] PASS — one barrel, one UUID, source(Maya/P4) + identity(prod) + runtime(Unreal).")


if __name__ == "__main__":
    main()
