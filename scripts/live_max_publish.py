"""Live 3ds Max -> Perforce proof (run under 3dsmaxbatch, NOT pytest).

Drives a REAL 3ds Max publish through the REAL p4 seams against a running assetcore
service: mint identity -> stamp into the .max custom fileProperties -> bind the
source facet to the live depot path -> verify the stamp round-trips and resolve()
reflects it -> p4 submit -> re-publish to advance the source version to a real CL.

The on-iron confirmation that the NEW MaxAdapter (already proven against fakes via
the identical DCC contract) works against real 3ds Max.

Run (with the service up — see docs/LIVE_PROVING.md):
    MB="/c/Program Files/Autodesk/3ds Max 2027/3dsmaxbatch.exe"
    "$MB" scripts/live_max_publish.py
3dsmaxbatch doesn't surface python stdout, so the outcome is written to
%TEMP%/assetcore_max_result.txt (PASS/FAIL + the step log).

Config via env (local defaults): ASSETCORE_URL, ASSETCORE_DEPS (<repo-parent>/
max-deps), P4_BIN, P4_WS, P4PORT/P4USER/P4CLIENT.
"""
import os
import subprocess
import sys
import tempfile
import traceback

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULTS = {
    "ASSETCORE_URL": "http://127.0.0.1:8765",
    "ASSETCORE_DEPS": os.path.join(os.path.dirname(_REPO), "max-deps"),
    "P4_BIN": os.path.join(os.path.dirname(_REPO), "p4local", "bin"),
    "P4_WS": os.path.join(os.path.dirname(_REPO), "p4local", "ws"),
    "P4PORT": "localhost:1666", "P4USER": "assetcore", "P4CLIENT": "assetcore_ws",
}
for k, v in _DEFAULTS.items():
    os.environ.setdefault(k, v)

sys.path[:0] = [_REPO, os.environ["ASSETCORE_DEPS"]]
os.environ["PATH"] = os.environ["P4_BIN"] + os.pathsep + os.environ.get("PATH", "")

P4 = os.path.join(os.environ["P4_BIN"], "p4.exe")
WS = os.environ["P4_WS"]
DOC = os.path.join(WS, "art", "props", "live_max_barrel.max")
RESULT = os.path.join(tempfile.gettempdir(), "assetcore_max_result.txt")

_log: list[str] = []


def step(msg):
    _log.append(f"[live-max] {msg}")


def flush(final):
    _log.append(final)
    with open(RESULT, "w", encoding="utf-8") as f:
        f.write("\n".join(_log) + "\n")


def p4(*args):
    return subprocess.run([P4, *args], capture_output=True, text=True, check=True).stdout.strip()


def main():
    from assetcore.integrations.max import MaxAdapter
    from assetcore.sdk.client import AssetcoreClient

    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with AssetcoreClient(token="artist-token", base_url=os.environ["ASSETCORE_URL"]) as client:
        adapter = MaxAdapter(client)   # REAL pymxs + REAL p4 seams

        step(f"publishing {DOC} (first publish: mint + stamp + bind_source)")
        aid = adapter.publish(DOC, "prop", "env_amy")
        step(f"minted identity: {aid}")

        stamped = adapter.read_stamp(DOC)
        assert stamped == aid, f"stamp round-trip failed: {stamped!r} != {aid!r}"
        step(f"stamp round-trips from the .max fileProperties: {stamped}")

        facets = client.resolve(aid)
        loc = facets["source"]["location_uri"]
        assert loc == adapter.current_location(DOC), f"source loc mismatch: {loc!r}"
        step(f"source facet bound to live depot path: {loc} (rev {facets['source']['revision']})")

        step("submitting the .max to Perforce, then re-publishing to advance the version")
        try:
            p4("add", DOC)
        except subprocess.CalledProcessError:
            p4("edit", DOC)
        p4("submit", "-d", "assetcore live max publish", DOC)
        cl = p4("-ztag", "-F", "%change%", "changes", "-m1", DOC)
        v2 = client.bind_source(aid, adapter.current_location(DOC), "max",
                                adapter.current_revision(DOC), "env_amy")
        step(f"re-published: source version {v2} now at real changelist {cl}")
        assert adapter.current_revision(DOC) == cl, "revision seam disagrees with p4"

    flush("[live-max] PASS — real 3ds Max -> P4 -> assetcore round-trip verified.")


try:
    main()
except Exception:
    flush("[live-max] FAIL\n" + traceback.format_exc())
    # exit non-zero so a wrapper/CI sees the failure, not just the result file
    sys.exit(1)
