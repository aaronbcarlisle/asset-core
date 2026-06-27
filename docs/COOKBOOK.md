# assetcore — Cookbook

Copy-paste recipes for **everything the system can currently do.** Each capability
is shown three ways:

- **In-process** — Python against `AssetcoreService` (no server, great for scripts/tests).
- **SDK / HTTP** — `AssetcoreClient` against a running service (what integrations use).
- **CLI / curl** — the `assetcore` command, or raw HTTP.

New here? Read [`DESIGN.md`](DESIGN.md) for the *why*, [`DEVELOPMENT.md`](DEVELOPMENT.md)
for the layout, then come back. The mental model in one line:

> An asset is a UUID. Three facets — **identity** (Production), **source**
> (Artist/DCC), **runtime** (engine) — hang off it. Relationships are a graph.
> Renames never move files. Everything below traffics in UUIDs, never paths.

---

## 0. Setup — pick a surface

### A. In-process (no server)

Build a service from a repo + a sink and call verbs directly. Use `InMemoryRepo`
for a throwaway, `SqliteRepo(":memory:")` / `SqliteRepo("studio.db")` for SQLite,
`PostgresRepo(dsn)` for Postgres — they are interchangeable.

```python
from assetcore.app.services import AssetcoreService
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink

svc = AssetcoreService(InMemoryRepo(), InMemorySink())

aid = svc.declare("prop", "amy")
svc.claim(aid, "Weathered Barrel", "props/env/barrels", "pat")
print(svc.resolve(aid)["identity"].display_name)   # -> 'Weathered Barrel'
```

In-process, IDs are `UUID` objects and `resolve()` returns **core entities**
(`svc.resolve(aid)["source"].location_uri`). Over HTTP everything is JSON
(strings/dicts). Authorities are **not** enforced in-process — that's an L2 concern.

### B. SDK client over HTTP

Start the service, then point a client at it. The client sends your authority token
on every call.

```bash
pip install -e ".[service]"
uvicorn assetcore.service.app:app --port 8000      # http://127.0.0.1:8000
```

```python
from assetcore.sdk.client import AssetcoreClient

with AssetcoreClient(token="artist-token", base_url="http://127.0.0.1:8000") as c:
    aid = c.declare("prop", "amy")                 # aid is a str here
    print(c.resolve(aid)["identity"])              # JSON dict (or None)
```

Pick the token for the authority you're acting as (see §1). A failing call raises
`httpx.HTTPStatusError`; read `err.response.status_code` / `.json()["detail"]`.

### C. CLI / curl

```bash
export ASSETCORE_URL=http://127.0.0.1:8000
export ASSETCORE_TOKEN=artist-token
assetcore declare --type prop --by amy            # prints the new id
assetcore resolve <id>
assetcore resolve <id> --json                     # raw service JSON for scripting
```

```bash
# raw HTTP — the token is a header; reads need no token
curl -s -X POST localhost:8000/assets \
  -H 'X-Assetcore-Token: artist-token' -H 'content-type: application/json' \
  -d '{"asset_type":"prop","created_by":"amy"}'
```

---

## 1. Authorities (who may do what)

The service maps a token → authority and enforces it per verb. **Reads are open**
(no token). Dev defaults:

| Token | Authority | Allowed mutations |
|---|---|---|
| `prod-token` | production | `claim`, `rename`, `deprecate` |
| `artist-token` | artist | `bind_source`, `declare`, `bulk_declare` |
| `engine-token` | engine | `bind_runtime`, `declare`, `bulk_declare` |
| `build-token` | build | `bind_runtime` |
| *(any of the above)* | — | `relate`, `set_binding`, `relocate`, `bulk_relate`, `bulk_relocate` |

Wrong authority → **403**; missing/invalid token on a guarded call → **401**.
Override the map with `ASSETCORE_TOKENS` (JSON `{token: authority}`).

---

## 2. Capability map

Every capability, every surface. Verbs below are linked to their recipe section.

| Capability | Service method | HTTP | CLI |
|---|---|---|---|
| [Declare](#declare) | `declare` | `POST /assets` | `declare` |
| [Resolve](#resolve) | `resolve` | `GET /assets/{id}` | `resolve` |
| [Claim](#claim) | `claim` | `POST /assets/{id}/claim` | `claim` |
| [Rename](#rename) | `rename` | `POST /assets/{id}/rename` | `rename` |
| [Bind source](#bind-source) | `bind_source` | `POST /assets/{id}/source` | `bind-source` |
| [Bind runtime](#bind-runtime) | `bind_runtime` | `POST /assets/{id}/runtime` | `bind-runtime` |
| [Relate](#relate) | `relate` | `POST /relate` | `relate` |
| [Set binding (float/pin)](#set-binding) | `set_binding` | `POST /set_binding` | — |
| [Resolve dependency](#resolve-dependency) | `resolve_dependency` | `GET /dependency` | — |
| [Used-by / lineage](#used-by--lineage) | `used_by` / `lineage` | `GET /assets/{id}/used_by` · `/lineage` | `used-by` · `lineage` |
| [Transitive impact](#transitive-impact-dependents--dependencies) | `dependents` / `dependencies` | `GET /assets/{id}/dependents` · `/dependencies` | `dependents`/`impact` · `dependencies` |
| [Stale derivations](#stale-derivations) | `stale_derivations` | `GET /assets/{id}/stale-derivations` | `stale-derivations` |
| [Floating dependencies](#floating-dependencies) | `floating_dependencies` | `GET /assets/{id}/floating-dependencies` | `floating` |
| [Relocate (move bytes)](#relocate) | `relocate` | `POST /assets/{id}/relocate` | `relocate` |
| [Deprecate](#deprecate) | `deprecate` | `POST /assets/{id}/deprecate` | `deprecate` |
| [Find similar](#find-similar) | `find_similar` | `GET /similar` | `find-similar` |
| [Backfill worklist](#backfill-worklist) | `backfill_worklist` | `GET /worklist/provisional` | `worklist` |
| [Bulk declare/relate/relocate](#bulk-operations) | `bulk_*` | `POST /bulk/declare` · `/relate` · `/relocate` | — |
| [Metrics / health](#metrics--health) | `metrics` | `GET /metrics` · `/health` | — |
| [Events (SSE)](#4-events--automation) | — | `GET /events` | `subscribe` |
| [Production tools](#5-production-tools-impact-previewed-moves) | `sdk.tools.*` | (composed) | `move` · `relocate-prefix` |
| [Open source / fetch](#open-source-jump-to-the-authored-file) | `sdk.tools.fetch_source` | (composed) | `open-source` |

---

## 3. The verbs

### Declare

Mint a new asset. It starts **provisional** (no identity yet) but already has a
durable UUID and an identity facet ready for backfill. `origin` is free-form
birth context (who/where/why) used later by `find_similar` and the worklist.

```python
# in-process
aid = svc.declare("prop", "amy", origin={"shot": "sq01", "dcc": "maya"})

# SDK
aid = c.declare("prop", "amy", origin={"shot": "sq01"})   # authority: artist or engine
```

```bash
assetcore declare --type prop --by amy            # prints the id
curl -s -X POST localhost:8000/assets -H 'X-Assetcore-Token: artist-token' \
  -H 'content-type: application/json' \
  -d '{"asset_type":"prop","created_by":"amy","origin":{"shot":"sq01"}}'
# -> {"id":"…"}  (HTTP 201)
```

### Resolve

UUID → all three facets in one lookup (replaces "dig through folders"). Returns
`meta`, `identity`, `source`, `runtime`; any facet may be `null` if unset.

```python
r = svc.resolve(aid)
r["meta"].lifecycle          # in-process: Lifecycle enum
r["source"].location_uri     # or None

r = c.resolve(aid)           # SDK: JSON
r["identity"]["display_name"]
r["source"] and r["source"]["version_num"]
```

```bash
assetcore resolve <id>
assetcore resolve <id> --json
curl -s localhost:8000/assets/<id>            # open — no token needed
```

A resolve of an unknown id → **404** over HTTP (`meta` is `None` in-process).

### Claim

Production gives a provisional asset meaning — the backfill step. Sets
`display_name` + `taxonomy`, flips lifecycle to **active**. `attributes` is an
authoritative set (claiming with none clears them).

```python
svc.claim(aid, "Weathered Barrel", "props/env/barrels", "pat",
          biome="harbor", reusable=True)         # **attrs become identity.attributes

c.claim(aid, "Weathered Barrel", "props/env/barrels", "pat",
        attributes={"biome": "harbor"})          # authority: production
```

```bash
assetcore claim <id> --name "Weathered Barrel" --taxonomy props/env/barrels --actor pat
```

### Rename

Relabel the **identity facet only** — no file moves, no engine changes. This is the
headline guarantee: identity is not the path. Pass `new_taxonomy` to also re-file it
taxonomically (still no bytes move).

```python
svc.rename(aid, "Mossy Barrel", "pat")                       # name only
svc.rename(aid, "Mossy Barrel", "pat", new_taxonomy="props/env/barrels/mossy")

c.rename(aid, "Mossy Barrel", "pat", new_taxonomy="props/env/barrels/mossy")
```

```bash
assetcore rename <id> --name "Mossy Barrel" --actor pat --taxonomy props/env/barrels/mossy
```

To move **bytes** (a `p4 move` / reorg) without changing identity, that's
[Relocate](#relocate) — a different facet, a different verb.

### Bind source

The artist/DCC publishes authored truth — writes the **source facet** and returns a
monotonic version number. Re-binding the same asset adds a new version and demotes
the prior latest (the `one_latest_source` invariant). `location_uri` is opaque
(`//depot/...`, `git://...`, …); `revision` is a string (P4 CL, git sha, …).

```python
v1 = svc.bind_source(aid, "//depot/art/barrel.ma", "maya", "1207", "amy")   # -> 1
v2 = svc.bind_source(aid, "//depot/art/barrel.ma", "maya", "1311", "amy")   # -> 2 (v1 demoted)

v = c.bind_source(aid, "//depot/art/barrel.ma", "maya", "1311", "amy")      # authority: artist
```

```bash
assetcore bind-source <id> //depot/art/barrel.ma --tool maya --rev 1311 --by amy
```

### Bind runtime

The build/engine reports where the cooked/imported asset lives — writes the
**runtime facet**, returns a version. Same versioning + latest semantics as source.

```python
v = svc.bind_runtime(aid, "/Game/Props/Barrel.uasset", "nightly-4821")

v = c.bind_runtime(aid, "/Game/Props/Barrel.uasset", "nightly-4821")        # authority: engine/build
```

```bash
assetcore bind-runtime <id> /Game/Props/Barrel.uasset --build nightly-4821
```

### Relate

Assert a **new** typed edge between two identities. `binding_mode`/`pinned_version`
are valid only on `DEPENDS_ON`. Self-edges and a binding_mode on a non-`DEPENDS_ON`
edge are rejected (**400** over HTTP). For `DERIVED_FROM`, the edge records the
parent's current source version so [staleness](#stale-derivations) can be detected
later.

```python
from assetcore.core.types import RelType, BindingMode

svc.relate(placed, master, RelType.INSTANCE_OF, "amy")
svc.relate(tavern, barrel, RelType.COMPOSED_OF, "amy")
svc.relate(normalmap, highpoly, RelType.DERIVED_FROM, "amy")
svc.relate(walk_robin, walk_batman, RelType.VARIANT_OF, "lee")
svc.relate(anim, rig, RelType.DEPENDS_ON, "lee", BindingMode.FLOAT)         # consume, floating

# SDK uses plain strings
c.relate(anim, rig, "DEPENDS_ON", binding_mode="float", actor="lee")
c.relate(tavern, barrel, "COMPOSED_OF", actor="amy")
```

```bash
assetcore relate <anim> <rig> DEPENDS_ON --mode float --actor lee
assetcore relate <tavern> <barrel> COMPOSED_OF --actor amy
```

The five relationship types and what they mean are in [`PIPELINE_MODEL.md`](PIPELINE_MODEL.md);
a quick table is in [`DEVELOPMENT.md`](DEVELOPMENT.md#4-the-data-model-quick-reference).

### Set binding

Flip an **existing** `DEPENDS_ON` edge between `float` (always get the latest) and
`pin` (lock to a specific version). This is the materials-bottleneck fix: float
during production to get upstream updates for free, pin before delivery. Errors if
the edge doesn't exist (use `relate` to create it).

```python
svc.set_binding(anim, rig, BindingMode.PIN, pinned_version=2)   # lock to v2 for ship
svc.set_binding(anim, rig, BindingMode.FLOAT)                   # back to latest

c.set_binding(anim, rig, "pin", pinned_version=2)
```

```bash
# HTTP (no CLI subcommand for set_binding)
curl -s -X POST localhost:8000/set_binding -H 'X-Assetcore-Token: artist-token' \
  -H 'content-type: application/json' \
  -d '{"from_asset":"<anim>","to_asset":"<rig>","binding_mode":"pin","pinned_version":2}'
```

### Resolve dependency

Given a `DEPENDS_ON` edge, return the **exact source version** a consumer should
load — the pin if pinned, else the current latest. Returns `null` if there's no
such edge or no matching version.

```python
sv = svc.resolve_dependency(anim, rig)        # SourceVersion | None (in-process)
sv = c.resolve_dependency(anim, rig)          # JSON dict | None
```

```bash
curl -s "localhost:8000/dependency?frm=<anim>&to=<rig>"
```

### Used-by / lineage

One-hop graph reads. **used_by** = who instances/composes/derives-from this
(`INSTANCE_OF`, `COMPOSED_OF`, `DERIVED_FROM` incoming) — "where is this used".
**lineage** = what this instances/derives-from/varies (`INSTANCE_OF`,
`DERIVED_FROM`, `VARIANT_OF` outgoing) — "where did this come from".

```python
svc.used_by(barrel)        # list[Relationship]
svc.lineage(normalmap)
c.used_by(barrel)          # list[dict]
```

```bash
assetcore used-by <barrel>
assetcore lineage <normalmap>
```

### Transitive impact (dependents / dependencies)

The blast radius. **dependents** walks edges *up* (everything that transitively
depends on this — "what breaks if I change/rename/retire it"); **dependencies**
walks *down* (everything this is built from). Both return nodes as
`{asset_id, depth, rel_type}` in BFS order. Filter with `rel_types` (comma-separated
over HTTP) and bound with `depth`.

```python
svc.dependents(barrel)                                   # all edge types, unbounded
svc.dependents(barrel, [RelType.COMPOSED_OF], max_depth=2)

c.dependents(barrel, rel_types=["COMPOSED_OF", "DEPENDS_ON"], depth=3)
c.dependencies(tavern)
```

```bash
assetcore dependents <barrel> --rel-types COMPOSED_OF,DEPENDS_ON --depth 3
assetcore impact <barrel>          # 'impact' is an alias for dependents
assetcore dependencies <tavern>
curl -s "localhost:8000/assets/<barrel>/dependents?rel_types=COMPOSED_OF&depth=3"
```

> `rel_types=None` traverses **all** edge types; an explicit empty list matches
> **nothing** (it won't silently widen to everything).

### Stale derivations

`DERIVED_FROM` children of this asset whose parent **source advanced past the
version they were baked at** — e.g. a normal map whose high-poly was re-sculpted.
Advisory: it flags what to re-derive, never auto-rebuilds. (Unknown bounds are never
flagged — staleness must be certain.)

```python
svc.stale_derivations(highpoly)     # list[Relationship] needing a re-bake
c.stale_derivations(highpoly)
```

```bash
assetcore stale-derivations <highpoly>
```

### Floating dependencies

The float footgun guard: this consumer's `DEPENDS_ON` edges still floating. A
delivery gate can require these be pinned before ship.

```python
svc.floating_dependencies(anim)
c.floating_dependencies(anim)
```

```bash
assetcore floating <anim>
```

### Relocate

Move the **bytes** in place — a `p4 move`, a directory reorg, an engine re-import
path change. Same identity, same version, same edges; only the facet's
`location_uri` changes. `facet` is `source` (default) or `runtime`; pass
`new_revision` to also bump the source revision. This is the counterpart to
[rename](#rename): rename changes the *label*, relocate changes the *location*,
neither touches identity.

```python
svc.relocate(aid, "//depot/art/props/barrel.ma", "pat")                       # source
svc.relocate(aid, "//depot/art/props/barrel.ma", "pat", new_revision="1450")
svc.relocate(aid, "/Game/Env/Props/Barrel.uasset", "pat", facet="runtime")

c.relocate(aid, "//depot/art/props/barrel.ma", "pat", new_revision="1450")    # any authority
```

```bash
assetcore relocate <id> //depot/art/props/barrel.ma --actor pat --rev 1450
assetcore relocate <id> /Game/Env/Props/Barrel.uasset --actor pat --facet runtime
```

Relocating a facet that doesn't exist yet → **400** ("no source facet to relocate").
For directory-wide moves across many assets, see
[production tools](#production-tools-impact-previewed-moves).

### Deprecate

Retire an identity (lifecycle → `deprecated`). Reversible (it's a flag, not a
delete) and never strips facets or edges — `dependents` still finds who's on it, so
the retire is safe and auditable. Check `dependents` first.

```python
svc.dependents(old_barrel)          # see who'd be affected
svc.deprecate(old_barrel, "pat")
c.deprecate(old_barrel, "pat")      # authority: production
```

```bash
assetcore dependents <old_barrel>     # look before you retire
assetcore deprecate <old_barrel> --actor pat
```

### Find similar

The reuse-over-rebuild **nudge** at declare time. Ranks existing assets whose
identity fields / type / origin share tokens with a name. Purely advisory — it
never infers or merges identity; a human chooses to reuse (relate the existing UUID)
or declare new.

```python
svc.find_similar("barrel", asset_type="prop")     # [(asset, identity, score), …]
c.find_similar("barrel", asset_type="prop")        # [{id, display_name, score, …}]
```

```bash
assetcore find-similar barrel --type prop
```

### Backfill worklist

The provisional queue Production grooms — assets declared but not yet claimed,
oldest first, with their birth context.

```python
svc.backfill_worklist()             # [(asset, identity), …]
c.backfill_worklist()               # [{id, asset_type, created_by, created_at, origin, …}]
```

```bash
assetcore worklist
```

### Bulk operations

The hundreds-of-assets reality. Best-effort loops over the verbs (each item
independent; not one transaction). Declare returns the minted ids in order; relate
and relocate return counts.

```python
ids = svc.bulk_declare([
    {"asset_type": "prop", "created_by": "amy"},
    {"asset_type": "prop", "created_by": "amy", "origin": {"shot": "sq02"}},
])
svc.bulk_relate([
    {"frm": tavern, "to": ids[0], "rel_type": "COMPOSED_OF", "actor": "amy"},
    {"frm": tavern, "to": ids[1], "rel_type": "COMPOSED_OF", "actor": "amy"},
])
# relocate moves an EXISTING facet, so publish source first (then reorg the dir)
for aid_, name in zip(ids, ("a.ma", "b.ma")):
    svc.bind_source(aid_, f"//depot/art/props/{name}", "maya", "1", "amy")
svc.bulk_relocate([
    {"asset_id": ids[0], "new_location_uri": "//depot/env/props/a.ma", "actor": "pat"},
    {"asset_id": ids[1], "new_location_uri": "//depot/env/props/b.ma", "actor": "pat"},
])
```

```python
# SDK — note the HTTP edge shape uses from_asset/to_asset
ids = c.bulk_declare([{"asset_type": "prop", "created_by": "amy"}])
c.bulk_relate([{"from_asset": tavern, "to_asset": ids[0],
                "rel_type": "COMPOSED_OF", "actor": "amy"}])
c.bulk_relocate([{"asset_id": ids[0],
                  "new_location_uri": "//depot/art/props/a.ma", "actor": "pat"}])
```

```bash
curl -s -X POST localhost:8000/bulk/declare -H 'X-Assetcore-Token: artist-token' \
  -H 'content-type: application/json' \
  -d '{"specs":[{"asset_type":"prop","created_by":"amy"}]}'   # -> {"ids":[…]}
```

### Metrics / health

Operational view: lifecycle mix, facet coverage %, provisional age, request
latency, events emitted. `/health` is a trivial liveness probe.

```python
from datetime import datetime, timezone
svc.metrics(datetime.now(timezone.utc))     # dict
```

```bash
curl -s localhost:8000/health         # {"status":"ok"}
curl -s localhost:8000/metrics
```

---

## 4. Events & automation

Every facet write emits an append-only `Event`. The default `BroadcastSink` makes
them subscribable over **SSE** at `GET /events` (catch-up replay after a sequence,
then live follow; reconnect resumes via `Last-Event-ID`).

### Tail the stream

```bash
assetcore subscribe                       # follow live
assetcore subscribe --after 0 --limit 5   # replay from the start, stop after 5
curl -N "localhost:8000/events?after_seq=0"
```

### Reactive recipes (this is where asset management becomes pipeline)

`sdk/automation.EventRouter` dispatches events to handlers you register; feed it
`stream_events(...)` to react to the live spine. The core never learns the recipe.

```python
from assetcore.sdk.automation import EventRouter, stream_events

router = EventRouter()

@router.on("source.published")
def on_publish(ev):
    print("queue a cook + notify the tracker for", ev["asset_id"])

@router.on("identity.claimed")
def on_claim(ev):
    print("mirror name to ShotGrid for", ev["asset_id"])

@router.on("*")                       # wildcard: an audit logger
def audit(ev):
    print("audit:", ev["event_type"])

# live: tail the service and dispatch forever (Ctrl-C to stop)
router.run(stream_events("http://127.0.0.1:8000", "artist-token"))
# bounded (tests / batch): router.run(events, limit=10)
```

A handler raising never sinks the stream (the error goes to stderr, other handlers
still fire). See `scripts/demo_automation.py` for a synthetic-stream version that
needs no server.

### Event types you can subscribe to

| `event_type` | Emitted by | Key payload |
|---|---|---|
| `declared` | declare | `asset_type` |
| `identity.claimed` | claim | `name` |
| `identity.renamed` | rename | `name` |
| `identity.deprecated` | deprecate | — |
| `source.published` | bind_source | `location_uri`, `version`, `tool` |
| `runtime.cooked` | bind_runtime | `location_uri`, `version` |
| `source.relocated` / `runtime.relocated` | relocate | `location_uri`, `facet` |
| `relationship.added` | relate | `to`, `rel_type`, `binding_mode` |
| `binding.changed` | set_binding | `to`, `binding_mode`, `pinned_version` |

Each event also carries `seq`, `event_id`, `asset_id`, `actor`, `occurred_at`.
Dedupe on `event_id` — delivery is at-least-once (a reconnect may redeliver).

---

## 5. Production tools (impact-previewed moves)

`sdk/tools.py` composes the verbs into the orchestrated operations production
actually runs — preview the blast radius, then act. The CLI's `move` /
`relocate-prefix` are thin wrappers; a GUI could be another.

### Rename + relocate in one safe op

```python
from assetcore.sdk import tools
from assetcore.sdk.client import AssetcoreClient

with AssetcoreClient("prod-token", "http://127.0.0.1:8000") as c:
    print(tools.impact_report(c, aid))          # {total, by_depth, dependents} — preview first
    tools.rename_relocate(c, aid, "pat",
                          new_name="Mossy Barrel",
                          source_uri="//depot/art/props/barrel.ma", source_rev="1450",
                          runtime_uri="/Game/Env/Props/Barrel.uasset")
    # the UUID is stable throughout; each step is optional and individually atomic
```

```bash
assetcore move <id> --actor pat --name "Mossy Barrel" \
  --source //depot/art/props/barrel.ma --source-rev 1450 \
  --runtime /Game/Env/Props/Barrel.uasset            # preview only (prints impact)
assetcore move <id> --actor pat --name "Mossy Barrel" --yes   # add --yes to apply
```

### Directory move (remap a path prefix across many assets)

Matches on a directory boundary (`//depot/art/props` does **not** match
`//depot/art/props2`).

```python
tools.plan_prefix_moves(c, asset_ids, "//depot/art/props", "//depot/env/props")  # preview
tools.relocate_prefix(c, asset_ids, "//depot/art/props", "//depot/env/props", "pat")
```

```bash
assetcore relocate-prefix //depot/art/props //depot/env/props \
  --ids <id1>,<id2>,<id3> --actor pat            # preview (omit --yes)
assetcore relocate-prefix //depot/art/props //depot/env/props \
  --ids <id1>,<id2>,<id3> --actor pat --yes      # apply
```

### Open source (jump to the authored file)

The artist "open the source that authored this" jump: resolve the source URI and
materialize it locally via the resolver registry (Perforce/git/local — the URI is
opaque to the core, the resolver turns it into bytes).

```python
tools.source_location(c, aid)                   # the URI, or None
tools.fetch_source(c, aid)                       # local path via default_registry()
```

```bash
assetcore open-source <id>            # prints the source URI
assetcore open-source <id> --fetch    # also materializes it locally and prints the path
```

---

## 6. Configuration & swapping backends

The active repo/tracker/etc. is chosen by `assetcore.toml`, not by code. Point the
service at one with `ASSETCORE_CONFIG`; it validates at startup.

```toml
# assetcore.toml
[repos.main]
provider = "sqlite"
config = { path = "studio.db" }       # or provider = "postgres", config = { dsn = "${PG_DSN}" }

[trackers.production]
provider = "shotgrid"
config = { url = "${SG_URL}", script = "${SG_SCRIPT}", api_key = "${SG_KEY}", project = "Demo" }
```

```bash
# validate before deploy — reports every problem at once (exit 0/1)
SG_KEY=… python -m scripts.validate_config assetcore.toml

# run the service against it
ASSETCORE_CONFIG=assetcore.toml uvicorn assetcore.service.app:app
```

Swapping ShotGrid↔Jira or sqlite↔Postgres is an edit here, no code change. Full
treatment + how to add a provider: [`PROVIDER_LAYER.md`](PROVIDER_LAYER.md) and
[`DEVELOPMENT.md`](DEVELOPMENT.md#8-extending-add-a-storage-backend-or-tracker-a-provider).

---

## 7. End-to-end: the three canonical scenarios

Worked with the in-process service (run as a script, or adapt to the SDK). These are
the same scenarios the suite proves in `tests/test_scenarios.py`.

### The barrel — reuse instead of copy-paste

```python
from assetcore.app.services import AssetcoreService
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink
from assetcore.core.types import RelType

svc = AssetcoreService(InMemoryRepo(), InMemorySink())

barrel = svc.declare("prop", "amy")
svc.claim(barrel, "Weathered Barrel", "props/env/barrels", "pat")
svc.bind_source(barrel, "//depot/art/barrel.ma", "maya", "1207", "amy")

# two sets reuse the SAME barrel via a live relationship — no new file
harbor = svc.declare("set", "amy"); svc.claim(harbor, "Harbor", "sets/harbor", "pat")
tavern = svc.declare("set", "amy"); svc.claim(tavern, "Tavern", "sets/tavern", "pat")
svc.relate(harbor, barrel, RelType.COMPOSED_OF, "amy")
svc.relate(tavern, barrel, RelType.COMPOSED_OF, "amy")

svc.used_by(barrel)                 # both sets — "where is this used"
svc.dependents(barrel)              # transitive blast radius before you touch it
```

### Robin's locomotion — three reuse semantics, one mechanism

```python
batman_walk = svc.declare("anim", "lee"); svc.claim(batman_walk, "Batman Walk", "anim/loco", "pat")

shared  = svc.declare("anim", "lee")      # use Batman's walk live
svc.relate(shared, batman_walk, RelType.INSTANCE_OF, "lee")

forked  = svc.declare("anim", "lee")      # fork with provenance
svc.relate(forked, batman_walk, RelType.DERIVED_FROM, "lee")

# "what breaks if I fix Batman's walk?"
svc.dependents(batman_walk)
```

### The materials bottleneck — float then pin

```python
from assetcore.core.types import BindingMode

rig = svc.declare("rig", "mo"); svc.claim(rig, "Hero Rig", "rigs/hero", "pat")
svc.bind_source(rig, "//depot/rigs/hero.ma", "maya", "1", "mo")     # v1

anim = svc.declare("anim", "lee"); svc.claim(anim, "Hero Run", "anim/hero", "pat")
svc.relate(anim, rig, RelType.DEPENDS_ON, "lee", BindingMode.FLOAT) # float: get updates free

svc.bind_source(rig, "//depot/rigs/hero.ma", "maya", "2", "mo")     # v2 published
svc.resolve_dependency(anim, rig).version_num                        # -> 2 (floating to latest)

svc.set_binding(anim, rig, BindingMode.PIN, pinned_version=2)       # lock before delivery
svc.floating_dependencies(anim)                                     # -> [] (clean to ship)
```

---

## 8. Errors & status codes

| Situation | HTTP | In-process |
|---|---|---|
| missing/invalid token on a guarded verb | 401 | n/a (auth is L2) |
| wrong authority for the verb | 403 | n/a |
| unknown asset id (resolve, claim, …) | 404 | `resolve` → `meta` is `None`; mutating verbs raise `ValueError` |
| invalid edge (self-edge, binding_mode on non-DEPENDS_ON), bad rel_type, relocate a missing facet | 400 | `ValueError` |
| event sink can't stream (non-broadcast sink) | 501 | n/a |
| declare success | 201 | returns the id |
| claim/rename/relocate/deprecate/set_binding success | 204 | returns `None` |

Over the SDK, a non-2xx raises `httpx.HTTPStatusError`:

```python
import httpx
try:
    c.claim(unknown_id, "X", "t", "pat")
except httpx.HTTPStatusError as e:
    print(e.response.status_code, e.response.json()["detail"])
```

---

## See also

- [`CLI.md`](CLI.md) — the full command reference and flags.
- [`PIPELINE_MODEL.md`](PIPELINE_MODEL.md) — disciplines, relationships, when to use which.
- [`LIVE_PROVING.md`](LIVE_PROVING.md) — driving real Maya/Max/Unreal/ShotGrid/Photoshop.
- [`DEVELOPMENT.md`](DEVELOPMENT.md) — setup, layout, the firewall, extending.
- [`PROVIDER_LAYER.md`](PROVIDER_LAYER.md) — config-driven swaps and writing providers.
