"""Live Photoshop -> Perforce proof (run with a normal python, service up).

Drives a REAL Photoshop publish through the real seams against a running service:
mint identity -> stamp into the .psd XMP metadata -> bind the source facet to the
live depot path -> verify the stamp round-trips and resolve() reflects it -> p4
submit -> re-publish to advance the version to a real changelist.

Concept art is the front of the pipeline; this is the on-iron confirmation that the
PhotoshopAdapter (already proven against fakes via the identical DCC contract) works
against real Photoshop. NOTE: Photoshop COM automation LAUNCHES the Photoshop app.

Run (with the service up):
    python -m uvicorn assetcore.service.app:app --port 8765   # (another shell)
    python scripts/live_photoshop_publish.py

Config via env (local defaults): ASSETCORE_URL, ASSETCORE_DEPS (<repo-parent>/
ps-deps, where comtypes is installed — the adapter drives the Photoshop COM ProgID
directly), P4_BIN, P4_WS, P4PORT/P4USER/P4CLIENT.
"""
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULTS = {
    "ASSETCORE_URL": "http://127.0.0.1:8765",
    "ASSETCORE_DEPS": os.path.join(os.path.dirname(_REPO), "ps-deps"),
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
DOC = os.path.join(WS, "art", "concept", "barrel_concept.psd")


def p4(*args):
    return subprocess.run([P4, *args], capture_output=True, text=True, check=True).stdout.strip()


def step(msg):
    print(f"[live-ps] {msg}", flush=True)


def main() -> int:
    from assetcore.integrations.photoshop import PhotoshopAdapter
    from assetcore.sdk.client import AssetcoreClient

    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with AssetcoreClient(token="artist-token", base_url=os.environ["ASSETCORE_URL"]) as client:
        adapter = PhotoshopAdapter(client)   # launches Photoshop (COM) + real p4 seam

        step(f"publishing {DOC} (mint + stamp .psd XMP + bind_source)")
        aid = adapter.publish(DOC, "concept", "art_amy")
        step(f"minted identity: {aid}")

        stamped = adapter.read_stamp(DOC)
        assert stamped == aid, f"stamp round-trip failed: {stamped!r} != {aid!r}"
        step(f"stamp round-trips from the .psd XMP: {stamped}")

        facets = client.resolve(aid)
        loc = facets["source"]["location_uri"]
        assert loc == adapter.current_location(DOC), f"source loc mismatch: {loc!r}"
        step(f"source facet bound to live depot path: {loc} (rev {facets['source']['revision']})")

        step("submitting the .psd to Perforce, then re-publishing to advance the version")
        try:
            p4("add", DOC)
        except subprocess.CalledProcessError:
            p4("edit", DOC)
        p4("submit", "-d", "assetcore live photoshop publish", DOC)
        cl = p4("-ztag", "-F", "%change%", "changes", "-m1", DOC)
        v2 = client.bind_source(aid, adapter.current_location(DOC), "photoshop",
                                adapter.current_revision(DOC), "art_amy")
        step(f"re-published: source version {v2} now at real changelist {cl}")
        assert adapter.current_revision(DOC) == cl, "revision seam disagrees with p4"

        print("\n[live-ps] PASS — real Photoshop -> P4 -> assetcore round-trip verified.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
