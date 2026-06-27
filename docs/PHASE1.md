# PHASE 1 — Pure Core (no I/O)

> First Claude Code session brief. Self-contained: everything needed to build
> Phase 1 with zero ambiguity. Read `docs/ARCHITECTURE.md` Parts 1–3 and Appendix
> A first for context; this brief is the executable checklist.

## Goal

Extract the working prototype's logic into a **pure, tool-agnostic core domain**
(L0) plus an in-memory repository, and prove it by porting the three scenario tests
to run against it with **zero database, in milliseconds**.

When this phase is done, the *meaning* of the entire system is expressed in pure
functions over plain data, testable without Postgres, FastAPI, or any tool. Every
later phase hangs off this.

## The one rule for this phase

**`core/` imports nothing but the Python standard library.** No `sqlite3`, no
`psycopg2`, no framework, no tool. If you reach for an import that does I/O, it
belongs in `infra/` or higher, not in `core/`. This is the firewall that the whole
architecture depends on — establish it now, while the core is small.

## What exists today (your starting material)

- `assetcore/api.py` — the prototype verbs. Mixes pure logic with DB calls. You are
  **separating** those two concerns, not rewriting from scratch.
- `assetcore/db/schema.sql` — the 5 tables. The dataclasses mirror these.
- `tests/test_scenarios.py` — the 3 scenarios + invariants, currently hitting
  SQLite. You are repointing these at the in-memory repo.

Do **not** delete the prototype files in this phase — they stay as the reference
and keep `demo.py` working until later phases supersede them.

## Files to create

```
assetcore/core/__init__.py
assetcore/core/types.py          # enums
assetcore/core/entities.py       # plain dataclasses (no behavior, no I/O)
assetcore/core/ports.py          # AssetRepo, EventSink protocols
assetcore/core/rules.py          # pure functions — the actual business logic
assetcore/app/__init__.py
assetcore/app/verbs.py           # the verbs, orchestrating rules through ports
assetcore/infra/__init__.py
assetcore/infra/inmemory_repo.py # AssetRepo + EventSink, dict-backed
tests/unit/__init__.py
tests/unit/test_rules.py         # pure-function tests (no repo even)
tests/unit/test_scenarios.py     # the 3 scenarios vs InMemoryRepo
```

---

## 1. `core/types.py` — enums

Mirror the schema's CHECK constraints exactly. String-valued enums so they
serialize trivially later.

```python
from enum import Enum

class Lifecycle(str, Enum):
    PROVISIONAL = "provisional"
    ACTIVE = "active"
    DEPRECATED = "deprecated"

class RelType(str, Enum):
    INSTANCE_OF = "INSTANCE_OF"
    DERIVED_FROM = "DERIVED_FROM"
    VARIANT_OF = "VARIANT_OF"
    COMPOSED_OF = "COMPOSED_OF"
    DEPENDS_ON = "DEPENDS_ON"

class BindingMode(str, Enum):
    FLOAT = "float"
    PIN = "pin"
```

## 2. `core/entities.py` — plain data

`@dataclass` types mirroring the tables. **No methods that do logic** (logic lives
in `rules.py`). Note the tool-agnostic rename from the prototype: `depot_path` →
`location_uri`, `dcc` → `tool`, `p4_changelist` → `revision` (str), `engine_path` →
`location_uri`. This is the Part-2.1 abstraction — the core stores opaque URIs.

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4
from .types import Lifecycle, RelType, BindingMode

def _now(): return datetime.now(timezone.utc)
def _new_id(): return uuid4()

@dataclass
class Asset:
    asset_type: str
    created_by: str
    id: UUID = field(default_factory=_new_id)
    lifecycle: Lifecycle = Lifecycle.PROVISIONAL
    origin: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)

@dataclass
class IdentityFacet:
    asset_id: UUID
    display_name: str | None = None
    taxonomy: str | None = None
    status: str | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)

@dataclass
class SourceVersion:
    asset_id: UUID
    location_uri: str          # opaque: //depot/...  git://...  s3://...
    tool: str                  # 'maya','blender','substance' (a label)
    revision: str              # P4 CL, git sha, etc. (str, not int)
    version_num: int
    is_latest: bool = True
    published_by: str | None = None
    published_at: datetime = field(default_factory=_now)

@dataclass
class RuntimeVersion:
    asset_id: UUID
    location_uri: str          # '/Game/...' or any engine address
    build_id: str
    version_num: int
    is_latest: bool = True
    cooked_at: datetime = field(default_factory=_now)

@dataclass
class Relationship:
    from_asset: UUID
    to_asset: UUID
    rel_type: RelType
    binding_mode: BindingMode | None = None   # DEPENDS_ON only
    pinned_version: int | None = None
    attributes: dict = field(default_factory=dict)

@dataclass
class Event:
    asset_id: UUID | None
    event_type: str
    payload: dict = field(default_factory=dict)
    actor: str | None = None
    occurred_at: datetime = field(default_factory=_now)
```

## 3. `core/ports.py` — the interfaces

`typing.Protocol` so implementations don't need to inherit. Minimal set the verbs
need. **`core` defines what it needs; it does not know how it's satisfied.**

```python
from typing import Protocol
from uuid import UUID
from .entities import (Asset, IdentityFacet, SourceVersion,
                       RuntimeVersion, Relationship, Event)
from .types import RelType

class AssetRepo(Protocol):
    def create_asset(self, asset: Asset, identity: IdentityFacet) -> None: ...
    def get_asset(self, asset_id: UUID) -> Asset | None: ...
    def get_identity(self, asset_id: UUID) -> IdentityFacet | None: ...
    def save_identity(self, identity: IdentityFacet) -> None: ...
    def set_lifecycle(self, asset_id: UUID, lifecycle) -> None: ...

    def add_source_version(self, v: SourceVersion) -> None: ...
    def source_versions(self, asset_id: UUID) -> list[SourceVersion]: ...
    def add_runtime_version(self, v: RuntimeVersion) -> None: ...
    def runtime_versions(self, asset_id: UUID) -> list[RuntimeVersion]: ...

    def add_relationship(self, r: Relationship) -> None: ...
    def upsert_relationship(self, r: Relationship) -> None: ...   # for set_binding
    def edges_from(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]: ...
    def edges_to(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]: ...
    def get_edge(self, frm: UUID, to: UUID, rel_type: RelType) -> Relationship | None: ...

class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...
```

## 4. `core/rules.py` — the pure business logic (the important part)

These are the invariants from ARCHITECTURE Part 2.2, as **pure functions over plain
data** — no repo, no I/O. They take current state in, return decisions out. This is
where the system's *meaning* lives and what `test_rules.py` exercises directly.

```python
from .entities import SourceVersion, Relationship
from .types import RelType, BindingMode, Lifecycle

def next_version_num(existing: list) -> int:
    """version numbers are monotonic per asset per facet."""
    return max((v.version_num for v in existing), default=0) + 1

def demote_latest(existing: list) -> None:
    """invariant: at most one is_latest per asset per facet.
    Mutates the prior latest to is_latest=False. (Caller persists.)"""
    for v in existing:
        if v.is_latest:
            v.is_latest = False

def resolve_dependency_version(edge: Relationship,
                               dep_versions: list[SourceVersion]) -> SourceVersion | None:
    """THE float/pin brain. Pure function of an edge + the dependency's versions.
    float -> latest authored; pin -> the pinned version. No database."""
    if edge.binding_mode == BindingMode.PIN:
        return next((v for v in dep_versions if v.version_num == edge.pinned_version), None)
    return next((v for v in dep_versions if v.is_latest), None)

def validate_relationship(r: Relationship) -> None:
    """binding_mode only on DEPENDS_ON; no trivial self-edges. Raise on invalid."""
    if r.binding_mode is not None and r.rel_type != RelType.DEPENDS_ON:
        raise ValueError("binding_mode is only valid on DEPENDS_ON edges")
    if r.from_asset == r.to_asset:
        raise ValueError("self-referential edge")

def can_overwrite_stamp(existing: str | None, incoming: str) -> bool:
    """never strip identity: a different existing stamp may not be replaced."""
    return existing is None or existing == incoming
```

Keep these *boring and total*. If you're tempted to pass a repo into one, it
belongs in `app/verbs.py`, not here.

## 5. `app/verbs.py` — orchestration

The verbs from `api.py`, re-expressed against the ports + rules. Each verb takes a
repo and event sink (injected), uses `rules.py` for decisions, persists via the
repo, emits an event. **This is the only place rules + persistence meet.**

Port these from `api.py` (same names, same behavior), signature pattern:

```python
def declare(repo, sink, asset_type, created_by, origin=None) -> UUID
def claim(repo, sink, asset_id, display_name, taxonomy, actor, **attrs) -> None
def rename(repo, sink, asset_id, new_name, actor, new_taxonomy=None) -> None
def bind_source(repo, sink, asset_id, location_uri, tool, revision, published_by) -> int
def bind_runtime(repo, sink, asset_id, location_uri, build_id) -> int
def relate(repo, sink, frm, to, rel_type, actor, binding_mode=None, pinned_version=None) -> None
def set_binding(repo, sink, frm, to, binding_mode, pinned_version=None) -> None
def resolve(repo, asset_id) -> dict                 # all three facets
def resolve_dependency(repo, frm, to) -> SourceVersion | None
def used_by(repo, asset_id) -> list[Relationship]
def lineage(repo, asset_id) -> list[Relationship]
```

Mapping from the prototype (Appendix A): `declare/claim/rename/relate/resolve/
used_by/lineage` port almost verbatim; `bind_source` loses the P4-specific params
in favor of `location_uri/tool/revision`; `resolve_dependency` delegates its core
decision to `rules.resolve_dependency_version`. The `_emit` helper becomes
`sink.emit(Event(...))`.

## 6. `infra/inmemory_repo.py` — dict-backed AssetRepo + EventSink

Implements both protocols with plain dicts/lists. ~80 lines. This is what makes the
tests need no database. Also expose `.events` as a list so tests can assert the
event spine fired.

```python
class InMemoryRepo:          # satisfies AssetRepo
    def __init__(self):
        self.assets, self.identities = {}, {}
        self.sources, self.runtimes, self.rels = [], [], []
    # ... implement each port method over these structures

class InMemorySink:          # satisfies EventSink
    def __init__(self): self.events = []
    def emit(self, event): self.events.append(event)
```

---

## Tests (the done-when, made concrete)

### `tests/unit/test_rules.py` — pure, no repo
- `next_version_num([]) == 1`; with versions present returns max+1.
- `demote_latest` leaves exactly zero `is_latest` after running on a single-latest list.
- `resolve_dependency_version`: float returns the `is_latest` one; pin returns the
  pinned `version_num`; pin to a missing version returns `None`.
- `validate_relationship` raises on a `binding_mode` with a non-DEPENDS_ON type and
  on a self-edge.
- `can_overwrite_stamp(None, x)` True; `(x, x)` True; `(x, y)` False.

### `tests/unit/test_scenarios.py` — the 3 scenarios vs InMemoryRepo
Port `tests/test_scenarios.py` almost verbatim, swapping `SqliteDB()` for
`InMemoryRepo()`/`InMemorySink()` and `api.*` for `app.verbs.*`. All must hold:
1. **Barrel:** declare→provisional; claim→active; castle COMPOSED_OF the *same*
   barrel (two composers, one identity); mossy DERIVED_FROM; `used_by`/`lineage`
   correct.
2. **Robin:** walk shared live (fixing Batman's walk shows both sets in `used_by`);
   grapple DERIVED_FROM; cape no lineage.
3. **Materials:** float resolves latest across a new bind; pin holds version through
   a later bind.
4. **Invariant:** `rename` changes identity facet only — source & runtime versions
   untouched.
5. **Invariant:** exactly one latest source version after three binds.

Plus one new event-spine assertion (now that the sink is inspectable):
6. After a declare + bind_source + claim, `sink.events` contains event_types
   `declared`, `source.published`, `identity.claimed` in order.

---

## Done-when checklist

- [ ] `assetcore/core/` exists and imports **only stdlib** (verify:
      `python -c "import ast,sys; [print(n) for ...]"` or just grep — no `sqlite3`,
      `psycopg2`, `fastapi`, tool names anywhere under `core/`).
- [ ] `core/rules.py` is pure functions only (no repo/sink parameters).
- [ ] `infra/inmemory_repo.py` satisfies both `AssetRepo` and `EventSink`.
- [ ] `app/verbs.py` re-expresses every prototype verb against ports + rules.
- [ ] `pytest tests/unit/ -q` is green, runs in well under a second.
- [ ] The prototype (`demo.py`, `tests/test_scenarios.py`) still runs untouched —
      Phase 1 adds the pure core alongside it; it doesn't break the seed.

## Out of scope for Phase 1 (resist these)

- No SQLite/Postgres repos yet (that's Phase 2 — but keep the port clean so they
  drop in).
- No FastAPI, no events-over-the-wire (Phase 3).
- No adapters, no tools (Phase 4+).
- No new relationship types or facet fields beyond what the schema already has.

## Suggested first prompt to Claude Code

> Read docs/ARCHITECTURE.md Parts 1–3 + Appendix A and PHASE1.md. Then implement
> Phase 1: create the pure `core/` (types, entities, ports, rules), the
> `app/verbs.py`, and `infra/inmemory_repo.py`, porting the logic from
> `assetcore/api.py` per the mapping in the brief. Keep `core/` stdlib-only. Then
> port the scenario tests to `tests/unit/` against InMemoryRepo and get
> `pytest tests/unit/` green. Don't touch the existing prototype files. Show me the
> rules.py and the failing-then-passing test run.
