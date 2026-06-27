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

## Unreal 5.8 -> assetcore (runtime facet)

The `unreal` module exists only inside a running editor, so this runs headless via
the editor commandlet rather than a standalone interpreter. It needs a UE project
(a minimal content-only `.uproject` with `PythonScriptPlugin` enabled is enough)
and `httpx` for the editor's Python (installed beside the repo in `ue-deps/`, added
to `sys.path` by the driver; override with `ASSETCORE_DEPS`).

```bash
UEPY="/d/UE/UE_5.8/Engine/Binaries/ThirdParty/Python3/Win64/python.exe"
"$UEPY" -m pip install --target ../ue-deps httpx
# with the service running:
UE="/d/UE/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
"$UE" /path/to/AssetcoreProbe.uproject -nullrhi -unattended -nosplash -nop4 \
    -ExecutePythonScript="$(pwd)/scripts/live_unreal_reconcile.py"
```

`scripts/live_unreal_reconcile.py` ensures a probe asset exists in `/Game`, mints +
stamps identity into each asset's metadata tag, runs `reconcile` (bind the runtime
facet for every stamped asset), and verifies `resolve()` reflects it. The editor's
own log (the verbose one) is `<project>/Saved/Logs/<Project>.log`; grep it for
`LIVE_UNREAL_RESULT:` and the `[live-unreal]` lines for the outcome.

## ShotGrid (Flow Production Tracking) -> assetcore (identity facet, both ways)

The tracker is a VIEW over the identity facet. The driver runs in a normal python
(it has `httpx` + FastAPI); `shotgun_api3` is installed beside the repo in
`sg-deps/` and added to `sys.path`.

One-time site setup:
- a **Script** (API User) with an **Application Key** (Admin Center -> Scripts ->
  "Change Key" reveals it). Use the *script's* Application Key, NOT a Personal
  Access Token (that's a different, user-based mechanism).
- the script's **Permission Group = API Admin** (so it can create Assets, and —
  optionally — create fields via the API).
- two custom **Text** fields on the `Asset` entity: `sg_assetcore_uuid`,
  `sg_taxonomy` (an API-Admin script can create these in code via
  `schema_field_create`; otherwise add them in the Assets grid UI).

Secrets live in a gitignored `sg.env` (see `sg.env.example`):

```bash
# sg.env (gitignored) — single-quote the key (it may contain shell-special chars)
export SHOTGRID_URL=https://<site>.shotgrid.autodesk.com
export SHOTGRID_SCRIPT=<exact script name>     # case-sensitive!
export SHOTGRID_API_KEY='<the script Application Key>'
export SHOTGRID_PROJECT='Demo: Game'           # SG Assets are project-scoped (id or name)
```

Then (with the service up). Note `source sg.env` can choke if the key has shell-
special chars; loading it in python is safer:

```bash
python - <<'PY'
import re, os, runpy
for l in open('sg.env'):
    l=l.strip()
    if l and not l.startswith('#'):
        l=re.sub(r'^export\s+','',l); k,_,v=l.partition('='); os.environ[k.strip()]=v.strip().strip('"').strip("'")
runpy.run_path('scripts/live_shotgrid_mirror.py', run_name='__main__')
PY
```

`scripts/live_shotgrid_mirror.py` does declare+claim -> `mirror()` (creates a real
SG Asset in the target project) -> simulates a production rename in SG -> `apply()`
(pulled back as an identity rename) -> `resolve()` confirms -> deletes the test
Asset. Look for `[live-sg] PASS`.

## 3ds Max 2027 -> Perforce

`pymxs` exists only inside a Max runtime, so this runs headless via `3dsmaxbatch`
(like Unreal's editor commandlet). Max's bundled python (3.13) has no `pip` by
default — bootstrap it once with `ensurepip`, then install `httpx` beside the repo
in `max-deps/` (the driver adds it to `sys.path`; `p4` is found via `P4_BIN`).

```bash
MAXPY="/c/Program Files/Autodesk/3ds Max 2027/Python/python.exe"
"$MAXPY" -m ensurepip
"$MAXPY" -m pip install --target ../max-deps httpx
# with the service running:
MB="/c/Program Files/Autodesk/3ds Max 2027/3dsmaxbatch.exe"
"$MB" "$(pwd)/scripts/live_max_publish.py"
```

`scripts/live_max_publish.py` mints identity -> stamps the `.max` custom
fileProperties -> binds source to the live depot path -> verifies the stamp
round-trips and `resolve()` reflects it -> `p4 submit` -> re-publishes to a real
changelist. `3dsmaxbatch` doesn't surface python stdout, so the outcome is written
to `%TEMP%/assetcore_max_result.txt` (read that for PASS/FAIL + the step log).
Config via the same env as the Maya driver, with `ASSETCORE_DEPS` defaulting to
`<repo-parent>/max-deps`.

## Cross-tool E2E — one barrel, one identity, three facets

The capstone: `scripts/live_e2e.py` (normal python, service up) drives ONE barrel
across two real tool runtimes and shows all three facets resolve to a single UUID,
each written by its own authority:

```bash
python scripts/live_e2e.py
```

- **artist / Maya** (mayapy): publish -> mints the identity, binds the SOURCE facet
  to the live P4 depot path; emits the asset id to a handoff file.
- **production**: `claim` -> the IDENTITY facet (display name + taxonomy).
- **engine / Unreal** (UnrealEditor-Cmd): carries the SAME id forward — stamps it
  onto a `/Game` asset and binds the RUNTIME facet (never mints a second identity).
- `resolve(aid)` -> identity + source(Maya/P4) + runtime(Unreal) on one UUID.

The orchestrator sequences the two headless tool processes and threads the minted
id between them (Maya can't talk to Unreal; the *core* is the only shared spine).
Per-tool deps are the Maya + Unreal sections above; config via `MAYAPY`, `UE_CMD`,
`UE_PROJECT`, `MAYA_DEPS`, `UE_DEPS`.

## Photoshop 2026 -> Perforce (Concept Art)

Concept art is the front of the pipeline. The driver runs in a normal python (it
has httpx); `comtypes` (for the Photoshop COM ProgID) is installed beside the repo
in `ps-deps/` and added to `sys.path`. The real seam drives the generic
`Photoshop.Application` COM ProgID via comtypes (version-agnostic — `photoshop-
python-api`'s version map can't resolve PS 2026), doing every op through
DoJavaScript (XMP stamp into the `.psd`).

```bash
python -m pip install --target ../ps-deps comtypes   # the only dep the adapter needs
# with the service running, and Photoshop signed in to Adobe (COM can't launch a
# half-authenticated app):
python scripts/live_photoshop_publish.py
```

`scripts/live_photoshop_publish.py` mints identity -> stamps the `.psd` XMP -> binds
source to the live depot path -> verifies round-trip + resolve() -> `p4 submit` ->
re-publishes to a real changelist. NOTE: this LAUNCHES the Photoshop app (COM
automation is not headless). Look for `[live-ps] PASS`.
