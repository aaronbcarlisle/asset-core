# Live Proving Runbook

Running assetcore against REAL tools (not fakes), to confirm the on-iron
behaviour the contract suite proves against faithful stand-ins. Each tool talks
HTTP to a running service, so step 0 is always "service up".

## 0. The service

```bash
python -m uvicorn assetcore.service.app:app --port 8765 --log-level warning
# health: curl http://127.0.0.1:8765/docs  -> 200
```

Dev auth tokens: `artist-token` (artist), `prod-token` (production),
`engine-token` (engine), `build-token` (build). Override with `ASSETCORE_TOKENS`.

## Local Perforce (see also the local-perforce memory)

Portable binaries, no installer/admin:

```bash
# server (background); free unlicensed tier is fine for <=5 users
p4local/bin/p4d.exe -r p4local/root -p 1666
# connect
export P4PORT=localhost:1666 P4USER=assetcore P4CLIENT=assetcore_ws
```

Depot `//depot`, workspace `assetcore_ws` -> `p4local/ws`.

> Real-p4 gotcha (found while proving): the global `-F "%field%"` formatter is
> unreliable — `-F "%path%"` returns nothing, `-F "%change%"` returns `"Change 1"`
> not `1`. Always prefix `-ztag`. The resolver and Maya seam now do.

## Maya 2027 -> Perforce

`mayapy` needs `httpx` (the client) and `p4` on PATH; the service runs in a normal
python (it has FastAPI). `httpx` is installed into an isolated `maya-deps/` dir
*beside* the repo (not inside it, so it never pollutes git) — this matches the
driver's `ASSETCORE_DEPS` default (`<repo-parent>/maya-deps`); override the env var
to install elsewhere.

```bash
# run from the repo root; ../maya-deps == the driver's default ASSETCORE_DEPS
"/c/Program Files/Autodesk/Maya2027/bin/mayapy.exe" -m pip install --target ../maya-deps httpx
# with the service running:
"/c/Program Files/Autodesk/Maya2027/bin/mayapy.exe" scripts/live_maya_publish.py
```

`scripts/live_maya_publish.py` drives a real publish: mint identity -> stamp the
`.ma` fileInfo -> bind the source facet to the live depot path -> verify the stamp
round-trips and `resolve()` reflects it -> `p4 submit` -> re-publish to advance the
source version to a real changelist. Config via env (`ASSETCORE_URL`, `P4_BIN`,
`P4_WS`, `P4PORT/P4USER/P4CLIENT`); local defaults match the setup above.

## Unreal 5.8, 3ds Max, ShotGrid

(added as each is proven)
