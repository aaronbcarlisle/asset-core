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

## Phase 5 — First real DCC + engine  ✅ (real-iron run gated)
- [x] `integrations/maya.py` (MayaAdapter / DCCAdapter), `integrations/unreal.py`
      (UnrealAdapter / EngineAdapter) — real code, lazy tool imports + injectable
      seams (maya.cmds / unreal.EditorAssetLibrary / p4), reshaped from the
      prototype stubs per Appendix A.
- [x] `PerforceResolver` (sdk/resolvers.py) with injectable runner + default_registry
- [x] **Same contract suite green against the real adapters** here, via faithful
      fakes of the tool APIs: MayaAdapter passes the DCC contract, UnrealAdapter
      the engine contract. Integrations-import-firewall test added.
- [~] Inside real Maya/Unreal/P4: gated tests in tests/integration/
      test_real_tools_gated.py — **skip** in this environment (no DCC/engine/p4
      installed), run on real iron. The literal end-to-end (Maya -> P4 -> Unreal,
      "Open Source" -> real .ma) needs that software and a live P4 depot.
- **Done when:** barrel goes Maya -> Perforce -> Unreal; "Open Source" lands on
  the real .ma. Adapter conformance proven here; the on-iron run is the remaining
  step, gated and ready.

## Phase 6 — The swap test (proof of decoupling)  ✅
- [x] `integrations/blender.py`, `integrations/substance.py` — **core untouched**:
      the only *production* code added was two L4 files (192 lines); `git diff` vs
      Phase 5 shows ZERO changes under core/app/infra/service/sdk. (Tests and this
      roadmap changed too — the invariant is about production layers, not the diff.)
- [x] Blender **and** Substance pass the identical DCC contract suite (now run
      across 5 DCC adapters: dict, sidecar, maya, blender, substance).
- [x] Cross-tool flow (tests/contract/test_swap_crosstool.py): a Blender barrel, a
      Maya animation, a floating Substance material — one unchanged core; the
      float dependency dissolves the bottleneck across tool boundaries.
- **Done when:** it's easy. ✅ Four methods + one parametrize line per tool; the
  thesis executed.

## Phase 7 — Tracker + human surfaces  ✅
- [x] `integrations/shotgrid.py` (ShotGridAdapter / TrackerAdapter) as a VIEW —
      proven by a contract test that it touches ONLY resolve/rename, never
      bind_*/relate (a recording client enforces "never path-driving").
- [x] Provisional-backfill worklist: `GET /worklist/provisional` (oldest-first,
      with origin context). The data surface a grooming UI consumes — no GUI built
      (this is the backend project).
- [x] `find_similar` dedupe nudge: new verb + `GET /similar` + `DCCAdapter.
      suggest_existing`. **Advisory only** (pure token-overlap rule; never infers
      identity from a name — anti-pattern #5 respected).
- [x] Float-footgun validation gate: `rules.floating_dependencies` + verb +
      `GET /assets/{id}/floating-dependencies`.
- New port method `list_assets(asset_type?, lifecycle?)` across all three repos;
      new behaviors covered on in-memory + sqlite via the shared scenario suite.

## Phase 8 — Hardening to finished product  ✅ (live-Postgres run gated)
- [x] Dependency firewall as a real gate: import-linter contracts in pyproject,
      `lint-imports` (3 kept) + a pytest wrapper; AST source-scan kept as backstop.
- [x] Stamp-coverage CI gate: `app/observability.stamp_coverage_gate` +
      `scripts/stamp_coverage_gate.py` (non-zero exit below threshold).
- [x] Event idempotency + reconnect: stable `Event.id` (dedupe key) + SSE
      `Last-Event-ID` resume over the durable catch-up log.
- [x] Event-driven reconciliation: `EngineAdapter.on_asset_saved` (save-hook path).
- [x] Observability: `GET /metrics` (lifecycle mix, source/runtime coverage,
      provisional age, request latency) + pure metric functions.
- [x] Alembic first migration (deferred since Phase 2) — portable, **verified by
      upgrade/downgrade against sqlite in the test suite**.
- [x] `infra/notify_sink.py` (Postgres LISTEN/NOTIFY, deferred since Phase 3) —
      code-complete, gated on a live server.
- [x] Backup/restore + resolver load-test runbooks: `docs/OPERATIONS.md`.
- [~] Live Postgres run: still gated — no server in the build environment. The
      postgres_repo + notify_sink are code-complete and the migration is verified;
      the on-iron run against a real depot/DB is the one remaining manual step.
- **Done when:** coverage enforced + monitored, DB recoverable, latency in budget.
  ✅ here for everything not requiring a live Postgres / real DCC.

## Phase 9 — Configuration-driven provider layer  ✅
- [x] Generic capability/provider registry `sdk/providers.py` (generalizes
      `ResolverRegistry`): capability → {name → factory}, chosen by config not code.
- [x] `sdk/settings.py` — loads `assetcore.toml`, expands `${ENV}` at load (secrets
      stay out of the file), builds cached providers by capability+instance.
      (Capability/section mapping is explicit per accessor — fixes the draft's
      `rstrip("s")` charset-strip bug that broke `source_vcs`.)
- [x] `integrations/jira.py` (`JiraAdapter`, ≤50 lines) as the proof-of-swap —
      ShotGrid↔Jira is one new L4 file + a toml edit; nothing below L4 moves.
- [x] ShotGrid + repo (sqlite/postgres/memory) providers registered; `service/app.py`
      builds its default repo through the registry (`ASSETCORE_CONFIG`), retiring the
      last hard-coded backend default — no `if/elif` in the composition root.
- [x] `assetcore.toml` — the single studio service-selection surface.
- [x] Contract test `tests/contract/test_providers.py`: registry mechanics + the
      ShotGrid↔Jira **config-only swap** (identical app code) + `${ENV}` expansion +
      tracker-stays-a-view. Firewall still **3 kept / 0 broken**.
- [x] Folded in audit cleanup: contract conftest `importorskip("fastapi")` so a bare
      install skips instead of erroring; `requires-python` bumped to `>=3.11` (tomllib).
- **Out of scope (deferred):** unifying `ResolverRegistry` into the new registry;
      wiring Perforce/git/runtime resolvers as providers; startup config validation.

---

## Status: phases 1–9 complete

Core is stable and tool-agnostic; adding a tool is a contract-tested L4 adapter
(proven by the Phase-6 diff touching only integrations/); the parallel-handoff
bottleneck is gone (float/pin + event spine); identity never decays into paths;
the system is observable, gated, and recoverable. Swapping any backing service —
tracker, storage repo — is now a `assetcore.toml` edit, not code (Phase 9).
Remaining work is operational (run against real Postgres + real Maya/Unreal/
Substance on iron) — staged behind skips, not architecture.

## Standing risks (carry forward — ARCHITECTURE Part 7.3 / Part 11)

1. Stamp coverage is existential — missing is recoverable, stripped is not.
2. Reconciliation lag — eventually-consistent runtime view; upgrade per Phase 8.
3. Floating references are a footgun — validation gates + pin escape hatch.
4. Provisional graveyard — backfill queue must be groomed, not a junk drawer.
5. Org change is the real cost — three departments accepting facet sovereignty.
