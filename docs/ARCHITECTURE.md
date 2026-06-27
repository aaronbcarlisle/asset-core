# assetcore — Complete Architecture

> From empty directory to finished product. This document is the blueprint:
> read it top to bottom and you understand the entire system, why every boundary
> sits where it does, and what to build in what order. It is written to be handed
> to Claude Code and built in phases.
>
> Companion docs: `DESIGN.md` (the *why* — production pain points and the core
> idea) and `ROADMAP.md` (task tracking). This document is the *what* and the
> *how*, end to end.

---

## Part 0 — The thesis, and the one rule that protects it

**The core knows nothing about any tool. Tools know how to speak to the core.**

Every studio pipeline that has ever rotted did so because the integration became
the architecture: the system learned what Maya does, how Unreal imports, what a
Substance graph looks like — and when those tools changed, the foundation cracked.

We invert it. The core is a small, tool-agnostic domain that models *identity,
facets, relationships, and events*. It has never heard of Maya. An integration is
nothing but **a translator that turns one tool's vocabulary into the core's
universal verbs.** Swapping Maya for Blender, or adding Houdini or Substance, is
writing a new translator against a stable API — a weekend, not a quarter.

The test of the whole design: **if an integration needs the core to know
something tool-specific, the design has leaked.** When that pressure appears, the
fix is never "add a Maya branch to the core" — it's "find the tool-agnostic
concept underneath and add *that*." Usually it's a new relationship type, a new
facet field, or a new event type — never a conditional.

This single rule is the spine of everything below.

---

## Part 1 — The layered architecture

Five concentric layers. Dependencies point **inward only**. The core domain at the
center depends on nothing; the outermost integrations depend on everything but are
depended on by nothing. This is what makes tools disposable.

```
┌─────────────────────────────────────────────────────────────────────┐
│  L4  INTEGRATIONS  (disposable, tool-specific)                       │
│      maya · blender · unreal · houdini · substance · shotgrid · p4   │
│      each is a TRANSLATOR: tool events  ->  core verbs               │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  L3  ADAPTER SDK  (the contract every integration builds on)   │  │
│  │      AssetcoreClient · stamping protocol · resolver protocol   │  │
│  │      DCCAdapter / EngineAdapter / TrackerAdapter base classes  │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │  L2  SERVICE  (the network boundary / the only door)     │  │  │
│  │  │      HTTP API (FastAPI) · auth per authority · events    │  │  │
│  │  │  ┌───────────────────────────────────────────────────┐  │  │  │
│  │  │  │  L1  APPLICATION  (the verbs / use cases)          │  │  │  │
│  │  │  │      declare claim rename bind_* relate resolve    │  │  │  │
│  │  │  │  ┌─────────────────────────────────────────────┐  │  │  │  │
│  │  │  │  │  L0  CORE DOMAIN  (pure, tool-agnostic)      │  │  │  │  │
│  │  │  │  │      Asset · Facet · Relationship · Event    │  │  │  │  │
│  │  │  │  │      Identity · Version · BindingMode        │  │  │  │  │
│  │  │  │  │      NO I/O. NO tool names. NO framework.    │  │  │  │  │
│  │  │  │  └─────────────────────────────────────────────┘  │  │  │  │
│  │  │  │      ports: AssetRepo, EventSink (interfaces)      │  │  │  │
│  │  │  └───────────────────────────────────────────────────┘  │  │  │
│  │  │      adapters: PostgresRepo, SqliteRepo, NotifyEventSink  │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### What each layer is, and the rule it enforces

**L0 — Core domain.** Plain data types and pure logic: what an `Asset` is, what
makes a `Relationship` valid, how a version becomes "latest," what `float` vs
`pin` *means*. No database, no HTTP, no framework, no tool. It defines **ports** —
interfaces like `AssetRepo` and `EventSink` — describing what it needs from the
outside world without knowing how they're implemented. *Rule: if it imports
anything I/O or tool-specific, it doesn't belong here.*

**L1 — Application.** The use cases — the verbs. `declare()`, `bind_source()`,
`relate()`, `resolve_dependency()`. Each orchestrates core domain logic through
the ports. This is where a transaction lives, where an event gets emitted after a
write. It depends on L0 only. *Rule: business rules live here or in L0, never
above.*

**L2 — Service.** The network boundary — the famous "only door." FastAPI exposes
each verb as an endpoint, authenticates the caller *as an authority* (Production /
artist / engine / build), and translates HTTP ↔ application calls. Also hosts the
event stream subscribers connect to. *Rule: this layer adds transport and auth,
never business logic.*

**L3 — Adapter SDK.** The contract integrations build against — a thin client
library (`AssetcoreClient`) wrapping the HTTP API, plus base classes that encode
the *shape* of each integration kind: `DCCAdapter`, `EngineAdapter`,
`TrackerAdapter`. These define the universal hooks (`on_publish`, `stamp`,
`read_stamp`, `reconcile`) that every tool must implement — so writing a new
integration is "fill in five methods," not "figure out the protocol." *Rule: the
SDK is tool-agnostic; it defines the silhouette every tool fits into.*

**L4 — Integrations.** Disposable translators. `maya.py` knows `cmds.fileInfo`;
`blender.py` knows custom properties; `unreal.py` knows metadata tags. Each
implements the L3 base class for its tool. *Rule: all tool-specific knowledge
lives here and nowhere else. Delete one and the system is unharmed.*

### Why this ordering is the whole game

Because dependencies point inward, **the core cannot accidentally depend on a
tool.** You literally cannot `import maya` from L0 — it would be a backwards
dependency, caught instantly in review (and by an import-linter in CI, see Part 8).
The architecture mechanically enforces the thesis. Tool churn is contained to L4
by construction, not by discipline.

---

## Part 2 — The core domain in detail (L0)

This is the heart. Get this right and everything above is mechanical.

### 2.1 The entities

```
Asset                       the immutable identity
  id            : UUID      minted once, never changes
  asset_type    : str       'prop','anim','material',... (a label, not behavior)
  lifecycle     : enum      provisional -> active -> deprecated
  origin        : dict      birth context (free-form, for backfill)

IdentityFacet               owned by Production
  display_name, taxonomy, status, tags, attributes

SourceVersion               owned by Artist/DCC  (versioned)
  location_uri  : str       opaque to the core: '//depot/...', 'git:...', 's3://...'
  tool          : str       'maya','blender','substance' (a label)
  revision      : str       tool/VCS revision id (P4 CL, git sha, ...)
  version_num, is_latest, published_by

RuntimeVersion              owned by Engine  (versioned)
  location_uri  : str       '/Game/...', or any engine's address scheme
  build_id, version_num, is_latest

Relationship                a typed directed edge
  from, to, rel_type        INSTANCE_OF|DERIVED_FROM|VARIANT_OF|COMPOSED_OF|DEPENDS_ON
  binding_mode              float|pin   (DEPENDS_ON only)
  pinned_version

Event                       append-only fact
  asset_id, event_type, payload, actor, occurred_at
```

**The critical abstraction: `location_uri`.** The core does not know what a
Perforce path is. It stores an opaque URI string and never parses it. A Maya
integration writes `//depot/art/barrel.ma`; a Blender-over-git shop writes
`git://repo@sha/barrel.blend`; an S3 shop writes `s3://bucket/key`. The core
treats all three identically — they are locations it hands back to whoever asked.
*Resolving* a URI to actual bytes is the integration's job, not the core's. This
is the single most important decoupling in the system: **the core stores
locations, integrations resolve them.**

### 2.2 The domain rules that live in L0 (pure functions)

These are tool-agnostic invariants — the actual "business logic," tiny and
testable without any I/O:

- **Latest-version rule.** Binding a new source/runtime version demotes the prior
  latest. Exactly one `is_latest` per asset per facet. (Pure given the prior set.)
- **Dependency resolution.** Given a `DEPENDS_ON` edge and the dependency's version
  list, return the version the consumer should load: latest if `float`, the pinned
  one if `pin`. (Pure function of edge + versions — this is `resolve_dependency`'s
  brain, and it never touches a database.)
- **Relationship validity.** `binding_mode` only on `DEPENDS_ON`; no self-edges
  except where meaningful; `DERIVED_FROM` must point at an existing identity.
- **Lifecycle transitions.** `provisional -> active` on claim; `-> deprecated`
  never deletes (lineage preserved).
- **Stamp-overwrite rule.** A different existing stamp may not be silently
  replaced (the "never strip identity" invariant, enforced where stamping is
  validated).

Keeping these as pure functions over plain data is what lets you test the entire
*meaning* of the system in milliseconds with no database, and what stops tool
churn from ever touching the rules.

### 2.3 The ports (interfaces L0 defines, outer layers implement)

```python
class AssetRepo(Protocol):
    def create_asset(self, asset: Asset) -> None: ...
    def get(self, asset_id: UUID) -> Asset | None: ...
    def add_source_version(self, v: SourceVersion) -> None: ...
    def add_runtime_version(self, v: RuntimeVersion) -> None: ...
    def add_relationship(self, r: Relationship) -> None: ...
    def source_versions(self, asset_id: UUID) -> list[SourceVersion]: ...
    def edges_from(self, asset_id, rel_type=None) -> list[Relationship]: ...
    def edges_to(self, asset_id, rel_type=None) -> list[Relationship]: ...
    # ... the minimal set the verbs need

class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...
```

L0 depends only on these shapes. `PostgresRepo`, `SqliteRepo`, and a future
`InMemoryRepo` (for tests) implement `AssetRepo`. `NotifyEventSink` (Postgres
LISTEN/NOTIFY) and `KafkaEventSink` implement `EventSink`. **The verbs are written
against the Protocol, so the storage and the event transport are swappable without
touching a line of business logic** — the same decoupling, applied to
infrastructure instead of tools.

---

## Part 3 — The universal verbs (L1) and why they're enough for any tool

The entire system is operated through a small fixed set of verbs. The claim that
makes the architecture work: **every workflow in any DCC, engine, or tracker
decomposes into these verbs.** If you ever find a tool workflow that doesn't, that
is a genuine design signal worth stopping for — but in practice everything maps.

| Verb                   | Authority        | Tool-agnostic meaning |
|------------------------|------------------|-----------------------|
| `declare`              | artist / engine  | "something now exists" → mint provisional identity |
| `claim`                | Production       | "this is what it is" → backfill identity facet |
| `rename`               | Production       | relabel identity facet only |
| `deprecate`            | Production       | retire, keep lineage |
| `bind_source`          | DCC              | "authored truth is now at this URI/revision" |
| `bind_runtime`         | engine / build   | "built form is now at this URI" |
| `relate`               | any              | assert a typed edge |
| `unrelate`             | any              | retract an edge |
| `set_binding`          | consumer         | flip a DEPENDS_ON edge float↔pin |
| `resolve`              | anyone           | UUID → all three facets |
| `resolve_dependency`   | DCC              | edge → exact version to load (float/pin) |
| `used_by` / `lineage`  | anyone           | graph traversal |
| `find_similar`         | DCC (at declare) | reuse-over-rebuild nudge (dedupe) |

### How tool actions map to verbs (the translation table)

This is the Rosetta Stone every integration follows. Note that wildly different
tools produce the *same* verb calls — that's the decoupling working:

| A user does this, in this tool        | The integration calls |
|---------------------------------------|------------------------|
| Saves/publishes a Maya scene          | `bind_source(uri=//depot/...ma, tool=maya, rev=CL)` |
| Saves/publishes a Blender file        | `bind_source(uri=git://...blend, tool=blender, rev=sha)` |
| Exports a Substance material          | `bind_source(uri=..., tool=substance, rev=...)` |
| Imports/creates an asset in Unreal    | `ensure_identity` + `bind_runtime(uri=/Game/...)` |
| Cooks a build                         | `bind_runtime(...)` for each cooked asset |
| References another asset in any DCC   | `relate(consumer, dep, DEPENDS_ON, float)` |
| Duplicates-and-modifies an asset      | `declare` new + `relate(new, orig, DERIVED_FROM)` |
| Drops a shared asset into a set       | `relate(set, asset, COMPOSED_OF)` |
| Production renames in the tracker     | `rename(...)` |
| Locks deps before delivery            | `set_binding(edge, pin, version)` |

Maya speaks `cmds`, Blender speaks `bpy`, Unreal speaks `unreal` — and they all
emit the same dozen verbs. **That is the entire point.** The core never learns a
new word when you add a tool.

---

## Part 4 — The Adapter SDK (L3): making a new integration trivial

This is where "swapping Maya for Blender is a weekend" is actually *delivered*.
The SDK gives every integration a base class so writing one is filling in a handful
of methods against a stable contract.

### 4.1 The three integration archetypes

Every production tool is one of three kinds. Each has a base class:

```python
class DCCAdapter(ABC):
    """Authoring tools: Maya, Blender, Houdini, Substance, ZBrush, Nuke.
    Owns the SOURCE facet for assets it authors."""
    @abstractmethod
    def read_stamp(self, doc) -> UUID | None: ...      # read identity off the file
    @abstractmethod
    def write_stamp(self, doc, asset_id: UUID): ...    # stamp identity into the file
    @abstractmethod
    def current_location(self, doc) -> str: ...        # the URI of this doc
    @abstractmethod
    def current_revision(self, doc) -> str: ...        # VCS revision of this doc
    # PROVIDED by the SDK (tool-agnostic), built on the four above:
    def publish(self, doc, asset_type, artist): ...    # stamp-if-needed + bind_source
    def reference(self, doc, dependency_id, mode): ... # relate DEPENDS_ON + resolve

class EngineAdapter(ABC):
    """Runtime targets: Unreal, Unity, Godot, proprietary engines.
    Owns the RUNTIME facet."""
    @abstractmethod
    def read_stamp(self, asset_path) -> UUID | None: ...
    @abstractmethod
    def write_stamp(self, asset_path, asset_id: UUID): ...
    @abstractmethod
    def list_assets(self) -> list[str]: ...
    # PROVIDED by the SDK:
    def ensure_identity(self, asset_path): ...         # stamp-if-editor-native
    def reconcile(self, build_id): ...                 # walk + bind_runtime each

class TrackerAdapter(ABC):
    """Production trackers: ShotGrid, ftrack, Kitsu, a spreadsheet.
    A VIEW over the IDENTITY facet — never drives paths."""
    @abstractmethod
    def push_identity(self, asset_id, fields): ...     # mirror identity -> tracker
    @abstractmethod
    def pull_identity(self, external_id) -> dict: ...  # tracker edits -> claim/rename
```

### 4.2 What this buys you

To onboard **Blender**, a developer writes exactly this — nothing more:

```python
class BlenderAdapter(DCCAdapter):
    STAMP_KEY = "assetcore_uuid"
    def read_stamp(self, doc):
        return bpy.context.scene.get(self.STAMP_KEY)          # custom property
    def write_stamp(self, doc, asset_id):
        bpy.context.scene[self.STAMP_KEY] = str(asset_id)
    def current_location(self, doc):
        return f"git://{repo}@{sha}/{bpy.data.filepath}"
    def current_revision(self, doc):
        return git_sha()
```

Four methods. `publish()`, `reference()`, the stamp-overwrite guard, the
bind_source call, the event emission — all inherited, all tool-agnostic. The same
four-method shape onboards Houdini (`hou`), Substance (`sd`), Nuke (`nuke`). **The
"weekend, not a quarter" promise is this base class.**

The stamping *mechanism* differs per tool (Maya `fileInfo`, Blender custom
property, Unreal metadata tag, a sidecar `.uuid` file for tools with no metadata
slot at all) — but that difference is sealed inside `read_stamp`/`write_stamp`.
Everything above those two methods is identical everywhere.

### 4.3 The sidecar fallback (so NO tool is un-integratable)

Some tools have no place to stash a UUID (a raw image, an FBX, a tool with a
locked format). The SDK provides a `SidecarStampMixin`: identity lives in a
`<file>.assetcore` sidecar next to the asset, keyed also in the DB by content
hash so a move that drops the sidecar can still be recovered by re-hashing. This
guarantees the model's reach is universal — there is no asset type the system
structurally cannot track.

---

## Part 5 — The event spine: how parallel handoff actually flows

The materials-bottleneck fix (Scenario 3) is half data model (float/pin) and half
*nervous system*. Here's the full mechanism end to end.

```
materials artist publishes v2 in Substance
        │
        ▼
SubstanceAdapter.publish()  ──►  POST /bind_source           (L2)
        │
        ▼
application.bind_source()  ──►  repo.add_source_version()     (L1)
        │                  └──►  event_sink.emit(source.published)
        ▼
Postgres INSERT into event  ──►  NOTIFY assetcore_events      (L0 port impl)
        │
        ▼
Service holds a LISTEN; fans out to subscribers (SSE/WebSocket)
        │
        ├──► Maya session subscribed to deps of "Captain Facial Anim"
        │        receives {source.published, asset: face_mat, v2}
        │        edge is FLOAT ► non-blocking toast: "materials v2 — refresh?"
        │        artist clicks ► reference() re-resolves ► p4/git sync ► reload
        │
        └──► Production dashboard updates "last authored" live
```

The key properties:

- **Publish is one call.** Materials does nothing but publish. No notifying anyone,
  no chasing modeling/rigging. The spine carries the news.
- **The consumer decides.** A floating consumer is offered the update; a pinned one
  is silently unaffected. The publisher never thinks about consumers at all —
  **dependency direction is inverted from the manual process**, which is exactly
  why the bottleneck dissolves.
- **No rebuild chain.** Nothing republishes model→rig→anim. The reference resolves
  to the new version directly. The chain existed only because data was *copied*
  down it; with references, there's nothing to copy.

Subscriptions are themselves just graph queries: "notify me about the latest-source
of every asset I have a `DEPENDS_ON float` edge to." The spine needs no special
per-tool logic — it's the same edges, read in the notify direction.


---

## Part 6 — Resolution: from UUID to actual bytes

A subtle but load-bearing piece. The core stores opaque `location_uri`s; something
must turn `//depot/art/barrel.ma@4101` into a file on the artist's disk. That
"something" must *not* be the core (it would have to learn Perforce, git, S3, ...).

The answer: a **resolver registry** in the SDK, keyed by URI scheme.

```python
resolver_registry.register("//", PerforceResolver())   # depot paths
resolver_registry.register("git://", GitResolver())
resolver_registry.register("s3://", S3Resolver())
resolver_registry.register("/Game/", UnrealResolver())  # engine-internal

def fetch(location_uri) -> LocalPath:
    return resolver_registry.for_uri(location_uri).fetch(location_uri)
```

Now the flow for "open the source of the thing I'm staring at in-editor":

```
read stamp off .uasset ──► resolve(uuid) ──► source.location_uri = //depot/...ma@4101
                                                  │
                                          fetch(uri) ──► PerforceResolver.fetch()
                                                  │
                                          local path ──► open in Maya
```

The core did the identity lookup; the resolver did the bytes. Add a git-based shop
and you register a `GitResolver` — the core is untouched. **Location resolution is
a plugin, exactly like tool integration is a plugin.** Same pattern, applied to
storage backends instead of authoring tools.

---

## Part 7 — Data & consistency model

### 7.1 Source of truth, precisely stated

- **Identity & relationships:** assetcore DB is *the* source of truth. Nothing else.
- **Source bytes:** the VCS (Perforce/git) is source of truth; assetcore stores a
  *pointer* (uri + revision). assetcore never holds bytes.
- **Runtime bytes:** the engine/build store is source of truth; assetcore stores a
  pointer. The engine is sovereign over its own organization.

assetcore is the **binding layer**, never the byte store. This keeps it small,
fast, and out of the way of the tools that are good at storing bytes.

### 7.2 Consistency choices

- **Identity facet:** strongly consistent. A claim/rename is immediately visible.
- **Source facet:** strongly consistent at publish time (synchronous bind_source).
- **Runtime facet:** *eventually* consistent via reconciliation (periodic walk).
  Designers reorganize in-editor continuously; we sync on a cadence (or on
  save-hook where the engine supports it). Production's runtime view lags by one
  reconcile interval — acceptable, and upgradable to event-driven per Part 9.
- **Events:** at-least-once delivery. Subscribers must be idempotent (dedupe on
  event id). A missed notify is recovered by a catch-up query against the event
  table on reconnect — the table is the durable log; NOTIFY is just the low-latency
  hint.

### 7.3 The non-negotiable invariants (enforced in L0 + CI)

1. Every asset has exactly one identity facet from birth.
2. At most one `is_latest` per asset per versioned facet.
3. A stamp is never silently overwritten by a different UUID.
4. `deprecate` never deletes — lineage edges survive forever.
5. The core never imports an integration, a framework, or an I/O library
   (enforced by import-linter in CI — see Part 8).

---

## Part 8 — Repository layout & the dependency firewall

```
assetcore/
  core/                    # L0 — pure. import-linter forbids outward imports.
    entities.py            #   Asset, Facet, Version, Relationship, Event (dataclasses)
    rules.py               #   pure functions: latest, resolve_dependency, validity
    ports.py               #   AssetRepo, EventSink protocols
    types.py               #   enums: Lifecycle, RelType, BindingMode
  app/                     # L1 — use cases (the verbs)
    verbs.py               #   declare/claim/bind_*/relate/resolve/...
    services.py            #   orchestration, transaction boundaries
  infra/                   # L0 port implementations
    postgres_repo.py
    sqlite_repo.py
    inmemory_repo.py       #   for fast tests
    notify_sink.py         #   Postgres LISTEN/NOTIFY EventSink
    kafka_sink.py          #   (later)
  service/                 # L2 — the only door
    app.py                 #   FastAPI wiring
    routes.py              #   verb endpoints
    auth.py                #   authority identity (Production/artist/engine/build)
    events.py              #   SSE/WebSocket fan-out
    schemas.py             #   request/response models
  sdk/                     # L3 — the contract integrations build on
    client.py              #   AssetcoreClient (HTTP wrapper)
    dcc_adapter.py         #   DCCAdapter base + publish/reference
    engine_adapter.py      #   EngineAdapter base + ensure_identity/reconcile
    tracker_adapter.py     #   TrackerAdapter base
    stamping.py            #   StampProtocol, SidecarStampMixin
    resolvers.py           #   resolver registry + Perforce/Git/S3/Unreal resolvers
  integrations/            # L4 — disposable translators (one file per tool)
    maya.py  blender.py  houdini.py  substance.py
    unreal.py  unity.py
    shotgrid.py  perforce.py
  db/
    schema.sql             # Postgres DDL (the 5 tables)
    migrations/            # Alembic
docs/
  DESIGN.md  ARCHITECTURE.md  ROADMAP.md
tests/
  unit/                    # L0/L1 — no I/O, millisecond
  integration/             # L2 + real Postgres
  contract/                # every L4 adapter passes the SAME adapter test suite
cli/
  assetcore_cli.py         # declare/resolve/relate from a terminal (great for smoke tests)
pyproject.toml
```

### The dependency firewall (CI-enforced)

An `import-linter` contract in CI encodes the inward-only rule:

```
core   may import:  (nothing internal)
app    may import:  core
infra  may import:  core
service may import: core, app, infra
sdk    may import:  (only stdlib + http; NOT core internals — talks via HTTP)
integrations may import: sdk
```

A PR that does `import maya` inside `core/` fails CI mechanically. **The thesis is
not a guideline you remember; it's a build gate that can't be forgotten.**


---

## Part 9 — The build plan: empty directory → finished product

Eight phases. Each ends at a **runnable, demonstrable** state — you never build for
weeks without something to smoke-test. Build phases 1–3 to prove the core; 4–6 to
prove the tool-agnostic promise; 7–8 to harden. Each phase lists its done-when.

### Phase 1 — Pure core (no I/O at all)
Build L0: `entities.py`, `types.py`, `rules.py`, `ports.py`. Plus an
`InMemoryRepo` so you can exercise everything. Port the existing prototype's logic
into pure functions.
**Done when:** the 3 scenarios + invariants pass as unit tests against
`InMemoryRepo`, in milliseconds, with zero database. *(You already have these tests
from the prototype — they port almost directly.)*

### Phase 2 — Application verbs + real storage
Build L1 `verbs.py`/`services.py` against the ports. Implement `SqliteRepo` and
`PostgresRepo` + `schema.sql` + first Alembic migration. Wire the same tests to run
against all three repos (in-memory, sqlite, postgres).
**Done when:** identical test suite is green across all three backends. This proves
the port abstraction holds — storage is now swappable.

### Phase 3 — The service (the only door)
Build L2: FastAPI exposing every verb, `auth.py` distinguishing authorities, the
SSE/WebSocket event fan-out backed by Postgres LISTEN/NOTIFY (`notify_sink.py`).
Add the `cli/` tool that talks to the service.
**Done when:** you can `assetcore declare`, `bind-source`, `relate`, `resolve`
from a terminal against a running service; and a second terminal `assetcore
subscribe` prints events live as the first emits them. *The materials nervous
system is now real, with no tools attached yet.*

### Phase 4 — The Adapter SDK + the contract test suite
Build L3: `client.py`, the three adapter base classes, `stamping.py`,
`resolvers.py`. Critically, write the **contract test suite** (Part 10): a single
parameterized test that ANY adapter must pass.
**Done when:** a trivial `FakeDCCAdapter` (stamps into a dict, "files" in a temp
dir) passes the full contract suite end-to-end through the live service. *The
integration shape is now proven without any real tool installed.*

### Phase 5 — First real DCC + first real engine
Implement `integrations/maya.py` (DCCAdapter) and `integrations/unreal.py`
(EngineAdapter) + `PerforceResolver`. Run the **same contract suite** against them
inside Maya/Unreal (headless where possible).
**Done when:** the end-to-end milestone closes — a barrel goes Maya → Perforce →
Unreal, a designer right-clicks "Open Source" in Unreal and lands on the real
`.ma`. *The thesis is now demonstrated on real tools.*

### Phase 6 — The swap test (the proof of decoupling)
Implement `integrations/blender.py` and `integrations/substance.py` against the
same SDK. Do nothing to the core, app, or service.
**Done when:** Blender passes the identical contract suite, and a barrel authored
in Blender is consumed by an animation in Maya with a floating material from
Substance — all three tools, one unchanged core. *This phase is the entire
argument of the project, executed. If it's easy, you won.*

### Phase 7 — Production tracker + the human surfaces
Implement `integrations/shotgrid.py` (TrackerAdapter) as a *view*. Build the
provisional-backfill worklist UI and the duplicate-candidate nudge at declare time
(`find_similar`). Add publish-time validation gates (the float footgun guard).
**Done when:** Production can claim/rename from ShotGrid (mirrored, not
path-driving), the provisional queue is groomed not a junk drawer, and an artist
declaring a "barrel" is shown existing barrels first.

### Phase 8 — Hardening to finished product
Auth/RBAC per authority hardened; event delivery idempotency + catch-up on
reconnect; reconciliation moved to event-driven where the engine supports save
hooks; observability (metrics on resolve latency, stamp-coverage %, provisional
age); the stamp-coverage CI gate (build fails if any shipped asset is unstamped);
backup/restore of the binding DB; load testing the resolver.
**Done when:** stamp coverage is monitored and enforced, the binding DB has
backup/restore runbooks, and resolve latency is within budget under realistic
asset counts. *This is "finished product" for a studio rollout.*

### What "finished product" means here
Not "feature-complete forever" — it means: **the core is stable and tool-agnostic,
adding any new tool is a contract-tested adapter, the parallel-handoff bottleneck
is gone, identity never decays into paths, and the whole thing is observable and
recoverable.** New tools after this are routine L4 work, not architecture.

---

## Part 10 — Contract testing: the mechanism that keeps integrations honest

This is how you guarantee every tool behaves identically and how you make new
integrations safe. **One test suite, parameterized over every adapter.**

```python
# tests/contract/test_dcc_contract.py
@pytest.mark.parametrize("adapter", [
    FakeDCCAdapter(), MayaAdapter(), BlenderAdapter(),
    HoudiniAdapter(), SubstanceAdapter(),
])
class TestDCCContract:
    def test_publish_mints_and_stamps(self, adapter, live_service):
        doc = adapter.new_doc()
        aid = adapter.publish(doc, "prop", "artist")
        assert adapter.read_stamp(doc) == aid          # stamped
        assert live_service.resolve(aid)["source"]      # source bound

    def test_republish_keeps_identity(self, adapter, live_service):
        doc = adapter.new_doc()
        aid = adapter.publish(doc, "prop", "artist")
        aid2 = adapter.publish(doc, "prop", "artist")   # save again
        assert aid == aid2                              # SAME identity, v2

    def test_stamp_never_overwritten(self, adapter):
        doc = adapter.new_doc()
        adapter.write_stamp(doc, uuid4())
        with pytest.raises(StampConflict):
            adapter.write_stamp(doc, uuid4())           # refuses

    def test_reference_creates_float_edge(self, adapter, live_service): ...
    def test_round_trip_resolves_to_source(self, adapter, live_service): ...
```

The payoff: **the definition of "a correct integration" is executable.** Onboarding
a tool isn't "hope it behaves like Maya" — it's "make it pass the contract suite."
Every adapter, real or fake, proves the same guarantees. This is what lets you trust
that swapping tools is safe, and it's why Phase 6 is a real test rather than a hope.

---

## Part 11 — Anti-patterns: how this design dies, so you can refuse it

Catalogue the leaks so future-you (and Claude Code) can name and reject them:

1. **A tool name in the core.** `if tool == "maya":` anywhere below L4. The fix is
   always a tool-agnostic concept (a facet field, a rel type), never a branch.
2. **The core parsing a `location_uri`.** The instant the core splits a path or
   reads `@CL`, it has learned Perforce. URIs are opaque; resolvers parse them.
3. **An integration reaching past the SDK** into app/core internals. It must go
   through the HTTP client. If the client can't express it, the *client* grows a
   method — the integration never bypasses it.
4. **Business logic creeping into L2/L4.** A rule about float/pin or lifecycle
   appearing in a route handler or a Maya hook. Rules live in L0/L1.
5. **Identity inferred from a name/path.** Any "match by filename" fallback. The
   only identity is the stamp; missing stamp → provisional, never guess.
6. **The binding DB holding bytes.** It stores pointers. The moment it caches asset
   bytes it's competing with Perforce and will lose.
7. **A new tool requiring a core change.** The loudest alarm. If onboarding a DCC
   touches anything but L4 + maybe a resolver, stop and find the leak.

If none of these are present, the design is intact and tool churn stays trivial —
which was the whole point.

---

## Appendix A — Mapping the prototype you already have onto this

The prototype (`api.py`, `schema.sql`, the tests) is not thrown away — it's the
seed of L0+L1+infra:

- `api.py`'s functions → split: pure bits (`resolve_dependency`'s logic, latest-
  version rule) move to `core/rules.py`; the orchestration becomes `app/verbs.py`.
- `schema.sql` → `db/schema.sql` essentially as-is (it's already the 5 tables).
- `connection.py`'s SqliteDB/PostgresDB → become `infra/sqlite_repo.py` /
  `infra/postgres_repo.py` implementing the `AssetRepo` port.
- `tests/test_scenarios.py` → `tests/unit/` (run vs InMemoryRepo) — they barely
  change, which is itself evidence the model was right.
- `integrations/maya.py` & `unreal.py` stubs → reshaped onto the L3 base classes.

So Phase 1 starts from working code, not a blank page.

## Appendix B — Technology choices (and why each is swappable)

| Concern        | Default choice            | Swappable because |
|----------------|---------------------------|-------------------|
| Binding store  | PostgreSQL                | hidden behind `AssetRepo` port |
| Event hint     | Postgres LISTEN/NOTIFY    | hidden behind `EventSink` port; → Kafka later |
| Service        | FastAPI + uvicorn         | L2 only; verbs don't know about it |
| Source VCS     | Perforce                  | a resolver scheme; git/S3 register alongside |
| Migrations     | Alembic                   | infra-local |
| Contract tests | pytest parameterized      | — |

Every default is a Phase-1 convenience, not a commitment. The ports and resolver
registry mean each can change without reaching the core — the same decoupling that
makes tools disposable makes infrastructure disposable too.
