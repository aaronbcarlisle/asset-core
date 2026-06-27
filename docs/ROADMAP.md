# assetcore — Roadmap

The phased build plan lives in full detail in **ARCHITECTURE.md Part 9**. This file
tracks status against those phases. Each phase ends at a RUNNABLE, demonstrable
state — never build for weeks without something to smoke-test.

## Prototype (done — the seed)

- [x] Core data model: 5 tables (`db/schema.sql`)
- [x] The API verbs (`api.py`)
- [x] Backend-agnostic DB layer (SQLite zero-setup + Postgres stub)
- [x] The 3 scenarios + invariants as passing tests
- [x] Narrated demo (`demo.py`)
- [x] Maya/Unreal integration stubs
- [x] Full architecture blueprint (`docs/ARCHITECTURE.md`)

## Phase 1 — Pure core (no I/O)  ✅
> Full task brief with file specs, signatures, and done-when checklist: **docs/PHASE1.md**
- [x] `core/entities.py`, `types.py`, `rules.py`, `ports.py`
- [x] `infra/inmemory_repo.py`
- [x] Port the 3 scenarios to unit tests vs InMemoryRepo (millisecond, no DB)
- **Done when:** scenarios + invariants pass with zero database. ✅

## Phase 2 — Verbs + real storage  ✅ (with two deferrals)
- [x] `app/verbs.py` against the ports
      — `app/services.py` **deferred to Phase 3**: its job is transaction
        boundaries, which only become real when the service composes verbs.
        Per-method atomicity (demote+insert in one tx) lives in the repos now.
- [x] `infra/sqlite_repo.py`, `infra/postgres_repo.py`
      — first **Alembic migration deferred to Phase 3** (when Postgres is the
        live target). The canonical `infra/schema.sql` is the bootstrap DDL today.
- [x] Same suite green across in-memory / sqlite; **postgres gated** behind
      `ASSETCORE_TEST_DSN` + psycopg2 (skips cleanly when absent, never silently
      passes). One shared scenario suite (`tests/scenarios_common.py`) runs on
      every backend.
- Resolved PHASE1 decision #4: the one-latest invariant moved out of the verb
  into the repos' `add_*_version` (write-time guarantee, matching the
  `one_latest_*` unique indexes).
- **Done when:** identical tests pass across the runnable backends (proves the
  port). ✅ in-memory + sqlite; postgres ready on a live DSN.

## Phase 3 — The service (the only door)  ✅ (with one deferral)
- [x] `service/` FastAPI exposing every verb; `auth.py` per authority
      (token->authority; claim/rename=production, bind_source=artist,
      bind_runtime=engine|build, relate/set_binding=any, reads open)
- [x] `app/services.py` (the Phase-2 deferral) — composition seam routes depend on
- [x] SSE event fan-out via `infra/broadcast_sink.py` (in-process, durable log +
      catch-up replay). **`infra/notify_sink.py` (Postgres LISTEN/NOTIFY) deferred
      to Phase 8** event hardening (needs a live Postgres); same EventSink port,
      swaps in unchanged.
- [x] `cli/assetcore_cli.py` (declare/bind-source/relate/resolve/subscribe)
- [x] `service/schemas.py` — typed wire models incl. ResolveResponse
      (closes the Phase-1 untyped-resolve flag)
- **Done when:** declare/bind/relate/resolve from terminal; second terminal
  `subscribe` prints events live. ✅ Demonstrated: uvicorn + CLI, a subscriber
  saw catch-up replay (seq 1-3) then a live `declared` (seq 4). Nervous system
  real, no tools attached.

## Phase 4 — Adapter SDK + contract tests  ✅
- [x] `sdk/client.py`, `dcc_adapter.py`, `engine_adapter.py`, `tracker_adapter.py`
- [x] `sdk/stamping.py` (StampConflict + guard + SidecarStampMixin), `sdk/resolvers.py`
      (registry + LocalFileResolver; real Perforce/Git/S3/Unreal -> Phase 5)
- [x] `tests/contract/` — the parameterized suite ANY adapter must pass, run
      through a live (TestClient-backed) service
- [x] `FakeDCCAdapter` **and** `FakeSidecarDCCAdapter` (two stamping mechanisms)
      pass the full DCC contract; `FakeEngineAdapter` passes the engine contract
- [x] SDK firewall test: sdk imports only stdlib + http, never core/app/infra
      (source-level stand-in for the Phase-8 import-linter)
- TrackerAdapter base shipped; its contract suite lands with Phase 7.
- **Done when:** integration shape proven with no real tool installed. ✅ 20
  contract tests green; the definition of "a correct adapter" is now executable.

## Phase 5 — First real DCC + engine
- [ ] `integrations/maya.py` (DCCAdapter), `integrations/unreal.py` (EngineAdapter)
- [ ] `PerforceResolver`
- [ ] Same contract suite green inside Maya/Unreal (headless where possible)
- **Done when:** barrel goes Maya -> Perforce -> Unreal; "Open Source" in editor
  lands on the real .ma. The end-to-end milestone.

## Phase 6 — The swap test (proof of decoupling)
- [ ] `integrations/blender.py`, `integrations/substance.py` — core untouched
- [ ] Blender passes the identical contract suite
- [ ] Cross-tool flow: Blender-authored asset, Maya animation, Substance material,
      floating dependency — one unchanged core
- **Done when:** it's easy. That ease IS the project's thesis, executed.

## Phase 7 — Tracker + human surfaces
- [ ] `integrations/shotgrid.py` (TrackerAdapter) as a VIEW (never path-driving)
- [ ] Provisional-backfill worklist UI
- [ ] `find_similar` dedupe nudge at declare time
- [ ] Publish-time validation gates (the float-reference footgun guard)

## Phase 8 — Hardening to finished product
- [ ] Stamp-coverage CI gate (build fails on any unstamped shipped asset)
- [ ] Event idempotency + catch-up on reconnect
- [ ] Event-driven reconciliation where engine save-hooks allow
- [ ] Observability: resolve latency, stamp-coverage %, provisional age
- [ ] Binding DB backup/restore runbooks; resolver load test
- **Done when:** coverage enforced + monitored, DB recoverable, latency in budget.

## Standing risks (carry forward — ARCHITECTURE Part 7.3 / Part 11)

1. Stamp coverage is existential — missing is recoverable, stripped is not.
2. Reconciliation lag — eventually-consistent runtime view; upgrade per Phase 8.
3. Floating references are a footgun — validation gates + pin escape hatch.
4. Provisional graveyard — backfill queue must be groomed, not a junk drawer.
5. Org change is the real cost — three departments accepting facet sovereignty.
