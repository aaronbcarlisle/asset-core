# assetcore — Design Document

> An identity-first asset management framework for game & film production pipelines.
> This document is the source of truth for *why* the system is shaped the way it is.
> Read it before changing the model. Every decision here was made against real
> production pain; the tables and verbs are consequences of these principles, not
> arbitrary structure.

---

## 0. The problem, stated plainly

Every studio "asset management" system the author has worked with (across multiple
game and film studios) is really just **file management** wearing a production-tracking
hat. They share a common set of failures:

1. **Identity is the file path.** The name, the disk location, and the engine path are
   forced to be the same string, so three departments must agree on one naming
   convention — and they fight over it forever. A rename means a batch-move script.

2. **No real relationships.** You cannot cheaply answer "where is this asset used,"
   "what is it derived from," "what breaks if I change this." That knowledge lives in
   people's heads. Folders are a tree; the real relationships are a graph.

3. **Duplicate explosion.** Because finding and reusing an existing asset is harder
   than rebuilding it, artists copy-modify. Years later there are hundreds of nearly
   identical barrels competing to be the standard, with no recorded lineage.

4. **Competing sources of truth.** Production thinks it owns identity. The game engine
   owns what actually ships (the cooked/baked data). The DCC files are where authored
   truth lives. Every tool tries to crown ONE of these as king, so the other two
   fight it through rename wars and re-import archaeology. (Real horror story: artists
   exporting from Unreal, re-editing in a DCC, re-importing — because they couldn't
   find the original authored file.)

5. **Non-linear handoff.** Unlike a clean games publish-down dependency chain, many
   departments work simultaneously. Materials starts after animation is underway, but
   animation wants those materials for facial work. The current "fix": manual Slack
   coordination + a slow chain of model→rig→anim republishing every time anything
   upstream changes.

6. **Production blocks artists / artists block production.** Production can't
   pre-create assets nobody knows are needed yet (an environment artist doesn't know
   they need a prop until they're already building it). So either artists wait on a
   field being set, or production scrambles to create-on-demand.

---

## 1. The one idea

**An asset is an immutable IDENTITY. Three sovereign FACETS hang off it. They are
bound by a shared UUID and reconcile through that UUID only — never through names
or paths. Nothing is inferred; each authority writes only its own facet.**

That's the whole system. Every feature below is this idea applied to a different pair
of authorities. The reason there is almost no conditional logic in the codebase is
that there is nothing to condition on — there is only "which facet are you, update
your pointer."

### The three facets and their sovereign owners

| Facet      | Owner            | Holds                                   | A rename here…           |
|------------|------------------|-----------------------------------------|--------------------------|
| `identity` | **Production**   | display name, taxonomy, status, tags    | …is one UPDATE. No files move. |
| `source`   | **Artist / DCC** | Perforce depot path + changelist, DCC   | …updates a pointer. UUID unmoved. |
| `runtime`  | **Game engine**  | engine path, build id                   | …is just the designer reorganizing. Identity intact. |

The key realization that resolves the "competing source of truth" war: **you do not
elect a winner.** All three are legitimately authoritative — over *different facets of
the same identity*. Designers renaming assets in-editor is not a deviation to prevent;
it is a sovereign authority to allow. Production's visibility and the designer's
freedom stop being in tension because they read/write different fields keyed to the
same ID.

### Why a UUID and not a smart name

The UUID is **opaque and immutable**. It is minted at the moment of *intent* (when an
artist needs the asset), not at the moment a file is saved. Artists never see or type
it — tools stamp it invisibly. Because identity is decoupled from name/path:

- Production renames freely → `facet_identity` UPDATE.
- Artists `p4 move`/rename freely → `facet_source_version` pointer update.
- Designers rename/move in-editor freely → reconciliation rewrites `facet_runtime_version`.

The naming war becomes *structurally impossible*, not merely discouraged.

---

## 2. The data model (see `assetcore/db/schema.sql`)

Five tables. The shape IS the philosophy.

- **`asset`** — the immutable identity. UUID, type, lifecycle
  (`provisional → active → deprecated`), birth context. Born once, never meaningfully
  mutated.
- **`facet_identity`** — Production's facet. One row per asset.
- **`facet_source_version`** — the artist's facet. **Versioned** (so we can pin/float).
- **`facet_runtime_version`** — the engine's facet. **Versioned**.
- **`relationship`** — the graph. Typed directed edges between identities.
- **`event`** — append-only spine. Every facet write emits one row. Audit trail AND
  pub/sub feed in one table.

### The relationship types (the graph folders can't be)

| Edge           | Meaning                                              | Propagation |
|----------------|------------------------------------------------------|-------------|
| `INSTANCE_OF`  | same asset used in two places                        | live — updates propagate |
| `DERIVED_FROM` | forked from another; lineage kept                    | none — edits don't propagate |
| `VARIANT_OF`   | sibling variant under a shared concept               | n/a |
| `COMPOSED_OF`  | a whole contains a part (ship → 100 props)           | n/a |
| `DEPENDS_ON`   | needs another to build (barrel → wood material)      | float or pin |

### The float/pin knob (the materials-bottleneck fix)

A `DEPENDS_ON` edge carries a `binding_mode`:
- **`float`** → always resolve to the latest authored source version. The consumer
  gets upstream updates for free, with no rebuild chain.
- **`pin`** → lock to a specific version. The consumer is stable, immune to upstream
  churn (e.g. right before delivering a shot).

The reason people bake-and-republish today is *fear of unwanted change*. Give them an
explicit "I want updates" vs. "I want stability" knob and the manual republish cycle
disappears. This single column is the entire fix for Scenario 3.

---

## 3. The API surface (see `assetcore/api.py`)

**The API is the only door.** No tool touches Perforce/engine paths directly to
establish identity. The moment something reads a path off disk to identify an asset,
all guarantees evaporate. Everything traffics in UUIDs.

| Verb                   | Who calls it          | Effect |
|------------------------|-----------------------|--------|
| `declare()`            | artist / editor       | mint provisional UUID, return immediately |
| `claim()`              | Production            | give a provisional asset meaning (backfill) |
| `rename()`             | Production            | identity facet UPDATE only |
| `bind_source()`        | DCC publish hook      | write source facet version |
| `bind_runtime()`       | build / reconciliation| write runtime facet version |
| `relate()`             | any authority         | add a typed edge |
| `resolve()`            | anyone                | UUID → all three facets |
| `resolve_dependency()` | DCC adapter           | DEPENDS_ON edge → exact source version (float/pin) |
| `used_by()` / `lineage()` | Production         | graph traversals |

---

## 4. The three scenarios, solved (see `tests/test_scenarios.py`)

These are the author's real production pain points. Each resolves through the same
four verbs with **zero special-casing**.

### Scenario 1 — the barrel
Environment artist `declare()`s a barrel mid-work (no production wait; row is
`provisional`). Production `claim()`s it later, async. The castle reuses the SAME
barrel via a `COMPOSED_OF` edge to the same UUID — no copy. A mossy variant is a new
identity with a `DERIVED_FROM` edge. `used_by()` / `lineage()` answer "where used" and
"where from" as graph queries.

### Scenario 2 — Robin's locomotion from Batman's
Robin's set is `COMPOSED_OF` three members, and the member edges carry the semantics:
his walk points at Batman's walk identity (`INSTANCE_OF`, live — fixing Batman's walk
impacts Robin); his grapple is `DERIVED_FROM` Batman's (forked); his cape twirl has no
inbound edge (unique). Three reuse semantics, one mechanism.

### Scenario 3 — the materials bottleneck
The animator's `DEPENDS_ON` edge floats during blocking, so when materials publishes
v2 the DCC picks it up with no model republish / rig update / rebuild chain. Before
delivery the animator pins to a version and ignores later churn. One column does it.

### Bonus — the re-import horror
`resolve()` on a UUID read off a `.uasset` returns all three facets at once.
"Where's my source" is one lookup even when the engine path is
`/Game/Junk/Bob/BP_Barrel_FINAL_USETHIS`, because identity was never the path.

---

## 5. Architecture (target production shape)

```
        ┌──────────────┐   declare/claim/rename       ┌─────────────────┐
        │  Production  │ ───────────────────────────▶ │                 │
        │  UI (over    │ ◀─────────────────────────── │                 │
        │  ShotGrid)   │   identity facet + graph      │  IDENTITY +     │
        └──────────────┘                               │  RELATIONSHIP   │
        ┌──────────────┐   bind_source                 │  STORE          │
        │  DCC adapters│ ───────────────────────────▶  │  (Postgres)     │
        │  Maya/Subst. │ ◀─────────────────────────── │                 │
        │              │   resolve / resolve_dependency │  - asset        │
        └──────────────┘                               │  - facet_*      │
        ┌──────────────┐   bind_runtime (reconcile)    │  - relationship │
        │  Engine      │ ───────────────────────────▶  │  - event        │
        │  (Unreal)    │ ◀─────────────────────────── │                 │
        └──────────────┘   resolve                     └────────┬────────┘
                                                                │ every write emits
                                                       ┌────────▼────────┐
                                                       │   EVENT SPINE   │
                                                       │ LISTEN/NOTIFY → │
                                                       │ NATS/Kafka      │
                                                       │ (subscriber     │
                                                       │  nudges)        │
                                                       └─────────────────┘
```

- **Identity + relationship store** — Postgres. Source of truth for IDs, facets, edges,
  versions. Knows nothing about file formats.
- **Resolver** — turns UUID + context ("pinned v4 in Maya format") into a concrete
  location. The only thing that knows where bytes physically live.
- **Event bus** — Postgres `LISTEN/NOTIFY` to start; NATS/Kafka when you outgrow it.
- **DCC adapters** — thin Maya/Unreal/Substance plugins. Traffic only in UUIDs/refs.
- **Production UI** — a *view* over the same API. ShotGrid reads/writes the identity
  facet via API; it no longer dictates filenames.

### Where existing tools fit (you don't replace them)
- **Perforce** stays the byte store for source — but the depot path becomes just
  `source_ref`, a location, not an identity. P4 moves are harmless.
- **ShotGrid** becomes a *view* over the identity facet, not the authority. It stops
  driving disk paths.
- The identity service is the small new thing in the middle that none of these own
  today: **the durable binding.** That's the actual gap. Everything else you have.

---

## 6. The honest tensions (do not skip)

1. **Stamp coverage is existential.** The model degrades gracefully for *missing*
   identity (provisional backfill), but NOT for *stripped* identity. Guard rails:
   a build that warns on UUID-less assets; a publish that refuses to overwrite an
   existing UUID. This is the #1 place to spend paranoia.
2. **Reconciliation lag.** If the engine sync is periodic, Production's runtime view is
   eventually-consistent. Usually fine; move to event-driven only when it isn't.
3. **Provisional graveyard.** Editor-native and DCC-native assets both pile into the
   backfill queue. It must be a *groomed, visible worklist*, not a junk drawer.
4. **Floating references are a footgun.** A bad upstream publish silently propagates.
   Want validation gates on publish + the pin escape hatch.
5. **Org change is the real cost.** The engineering is weeks. Getting three departments
   to accept "you are each sovereign over your facet and blind to the others' names"
   is the actual project — but it's an *easier* sell than usual, because everyone gets
   MORE autonomy, not a shared convention to obey.

---

## 7. Build order (smallest slice that proves the thesis first)

1. **Identity service** — Postgres + the API verbs. (Mostly done — see code.) Add
   FastAPI/asyncpg, auth, tests.
2. **UUID stamping in DCC + engine** — Maya publish writes UUID into the file; Unreal
   import/create writes UUID into asset metadata. Highest *integration* effort; lives
   in finicky toolchains. Conceptually tiny. (Stubs provided.)
3. **Resolver** — UUID → location per facet. Thin.
4. **Engine reconciliation sync** — walk project, read UUIDs, `bind_runtime()`.
   Scheduled job to start.
5. **ShotGrid / Perforce adapters** — point existing tools at the service.

**First milestone that proves everything:** one barrel goes artist → Perforce →
engine, and a designer right-clicks "Open Source" in the editor and lands on the real
`.ma`. If that loop closes, every relationship type and the float/pin machinery are
additive on the same spine — not rewrites.

---

## 8. Glossary of decisions (quick reference for future-you)

- **Why provisional state?** So artists never wait on production. Existence and meaning
  are decoupled in time.
- **Why version source & runtime but not identity?** Identity is conceptual and
  singular; source/runtime are concrete artifacts that change over time and need
  pin/float.
- **Why an event table instead of just an audit log?** It's both: the same append-only
  rows are your history AND your subscriber feed (LISTEN/NOTIFY on insert).
- **Why edges instead of a parent_id / folder column?** Folders are a tree; reuse,
  derivation and dependency form a graph. One asset has many parents in many senses.
- **Why "the API is the only door"?** The single rule that keeps identity from
  decaying back into paths. Break it once and the guarantees unravel.
