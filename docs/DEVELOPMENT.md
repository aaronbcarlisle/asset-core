# assetcore — Development Guide

How to set up, run, test, and extend the codebase without eroding the one idea it
exists to protect: **the core knows no tools; tools know how to speak to the core.**

If you are new here, read in this order:

1. [`DESIGN.md`](DESIGN.md) — the *why*: the production pain, the core idea, three worked scenarios.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — the blueprint: the layers, the verbs, the adapter SDK, the firewall.
3. **This file** — how to actually work in the tree.
4. [`COOKBOOK.md`](COOKBOOK.md) — copy-paste examples for every capability.

The **API Reference** (signatures + docstrings for every module) is generated from
the source and browsable in the built site — see [§12](#12-building-the-docs-site)
to build it locally with `mkdocs serve`.

Reference docs: [`PIPELINE_MODEL.md`](PIPELINE_MODEL.md) (disciplines & relationships),
[`CLI.md`](CLI.md), [`PROVIDER_LAYER.md`](PROVIDER_LAYER.md) (config/swap),
[`OPERATIONS.md`](OPERATIONS.md) (firewall/migrations/backup), [`LIVE_PROVING.md`](LIVE_PROVING.md)
(driving real Maya/Max/Unreal/ShotGrid/Photoshop), [`ROADMAP.md`](ROADMAP.md).

---

## 1. Prerequisites

- **Python ≥ 3.11** (the config layer uses stdlib `tomllib`, 3.11+ only).
- Git. That's it for the zero-setup path — the default backend is in-memory SQLite.

### Install

The package is dependency-light by design; everything beyond the core is an
*optional extra*, so a bare install runs the pure domain with nothing to set up.

```bash
# zero-setup: pure core + verbs + sqlite, runs the demo and most tests
pip install -e .

# the HTTP service + SDK client (FastAPI, uvicorn, httpx)
pip install -e ".[service]"

# everything a contributor needs (service + import-linter + alembic + sqlalchemy + docs)
pip install -e ".[dev]"

# optional targets
pip install -e ".[postgres]"     # psycopg2 for the Postgres backend
pip install -e ".[migrations]"   # alembic + sqlalchemy
pip install -e ".[docs]"         # mkdocs-material + mkdocstrings (the docs site)
```

The extras map (see `pyproject.toml`):

| Extra | Pulls in | Needed for |
|---|---|---|
| *(none)* | `pytest` | core, verbs, sqlite repo, the demo, unit tests |
| `service` | fastapi, uvicorn, httpx | the L2 service, the SDK client, the CLI's live commands |
| `postgres` | psycopg2-binary | the Postgres repo |
| `migrations` | alembic, sqlalchemy | managed schema migrations |
| `docs` | mkdocs-material, mkdocstrings[python] | building the documentation site (§12) |
| `dev` | all of the above + import-linter | full contributor workflow |

---

## 2. Running it

```bash
python demo.py                  # narrated walkthrough of the 3 canonical scenarios (no DB)
python -m pytest tests/         # the suite — in-memory SQLite, zero external setup
python -m pytest tests/ -q      # quiet

# the HTTP service (needs the `service` extra). Default backend: sqlite :memory: (ephemeral)
uvicorn assetcore.service.app:app                 # → http://127.0.0.1:8000
uvicorn assetcore.service.app:app --port 8765     # any port; the SDK/CLI just need the URL

# the CLI (installed console script) — talks to a running service over HTTP
assetcore --help
assetcore resolve <id>
python -m assetcore.sdk.cli resolve <id>          # same thing without installing the script

# narrated workflow demos (in-process, no service needed)
python scripts/demo_environment.py
python scripts/demo_animation.py
python scripts/demo_automation.py
```

### Service configuration (environment)

The default `create_app()` is the runnable-here choice: SQLite `:memory:` + the
in-process `BroadcastSink`. Override with environment variables — no code change:

| Env var | Effect |
|---|---|
| `ASSETCORE_SQLITE_PATH` | file-backed SQLite instead of `:memory:` (durable) |
| `ASSETCORE_CONFIG` | path to an `assetcore.toml`; the repo is built from it and validated at startup (see [`PROVIDER_LAYER.md`](PROVIDER_LAYER.md)) |
| `ASSETCORE_TOKENS` | JSON `{token: authority}` map, replacing the dev defaults |

---

## 3. Repository layout — the layers

Dependencies point **inward only**. `core` imports nothing internal; `integrations`
import only `sdk`. This is mechanically enforced (see §5).

```
assetcore/
  core/          L0  pure domain — entities, types, rules, ports. NO I/O, NO tool names.
    entities.py        the 5 dataclasses (Asset, IdentityFacet, SourceVersion, RuntimeVersion, Relationship, Event)
    types.py           the enums (Lifecycle, RelType, BindingMode) — string-valued
    rules.py           all business logic as pure functions (version math, float/pin, graph walk, staleness, dedupe)
    ports.py           the interfaces the outside world must satisfy (AssetRepo, EventSink)
  app/           L1  the verbs — orchestration over the ports
    verbs.py           declare/claim/rename/bind_*/relate/resolve/dependents/relocate/deprecate/bulk_* …
    services.py        AssetcoreService — bundles (repo, sink) and exposes the verbs as methods
    observability.py   metrics helpers (coverage %, lifecycle counts, provisional age)
  infra/         L1  adapters for the ports (still "inside" — no HTTP, no tools)
    inmemory_repo.py   InMemoryRepo + InMemorySink (zero-dependency, great for tests/examples)
    sqlite_repo.py     SqliteRepo (the default backend; translates the Postgres DDL on the fly)
    postgres_repo.py   PostgresRepo (production target)
    broadcast_sink.py  BroadcastSink (subscribable — powers SSE /events)
    notify_sink.py     NotifySink (Postgres LISTEN/NOTIFY)
    _providers.py      registers the repo providers (sqlite/postgres/memory) with the registry
  service/       L2  the only door — FastAPI
    app.py             create_app() — the composition root; `app` is the default instance
    routes.py          one route per verb + the SSE /events stream
    schemas.py         pydantic request/response models (the wire contract)
    auth.py            token → authority, per-verb authority enforcement
    events.py          the SSE event_source generator
  sdk/           L3  the contract — what integrations are allowed to touch
    client.py          AssetcoreClient — one method per verb, over HTTP
    cli.py             the `assetcore` CLI (argparse over the client)
    dcc_adapter.py     DCCAdapter base — the tool-agnostic publish/reference flow
    tools.py           production tools (impact_report, rename_relocate, relocate_prefix, fetch_source)
    automation.py      EventRouter + stream_events (event-driven recipes)
    resolvers.py       ResolverRegistry + LocalFileResolver/PerforceResolver (URI → bytes)
    providers.py       the generic capability→provider registry
    settings.py        load/validate assetcore.toml, expand ${ENV}, build providers
  integrations/  L4  disposable translators — maya, max, blender, substance, unreal, photoshop, shotgrid, jira
  db/            schema.sql (Postgres dialect) + alembic migrations

docs/            the documents (this file lives here)
scripts/         operational drivers — live_* (drive real tools), demo_* (narrated), validate_config, stamp_coverage_gate
tests/           unit/ · contract/ · integration/ + the protected prototype scenarios
demo.py          narrated end-to-end run (the seed)
```

### The protected prototype seed

`assetcore/api.py`, `assetcore/db/schema.sql`, `demo.py`, and
`tests/test_scenarios.py` are the original single-file prototype. They are kept
**runnable and untouched** as a reference and a regression anchor — do not edit
them. New work lives in the layered tree above.

---

## 4. The data model (quick reference)

An asset is an immutable **identity** (a UUID). Three sovereign **facets** hang off
it, each owned by one authority, bound only by the UUID. Nothing is inferred.

| Entity | Owner | Key fields | Notes |
|---|---|---|---|
| `Asset` | — | `id`, `asset_type`, `lifecycle`, `created_by`, `origin` | the immutable identity; only `lifecycle` ever changes |
| `IdentityFacet` | Production | `display_name`, `taxonomy`, `status`, `tags`, `attributes` | a **rename touches only this** |
| `SourceVersion` | Artist/DCC | `location_uri`, `tool`, `revision`, `version_num`, `is_latest` | authored truth; versioned; location is **opaque** |
| `RuntimeVersion` | engine/build | `location_uri`, `build_id`, `version_num`, `is_latest` | the cooked/imported asset; versioned |
| `Relationship` | any | `from_asset`, `to_asset`, `rel_type`, `binding_mode`, `pinned_version`, `attributes` | a typed, directed edge |
| `Event` | — | `asset_id`, `event_type`, `payload`, `actor`, `id` | append-only; every facet write emits one |

**Enums** (`core/types.py`, all string-valued so `RelType.DEPENDS_ON == "DEPENDS_ON"`):

- `Lifecycle`: `provisional` → `active` → `deprecated`
- `RelType`: `INSTANCE_OF`, `DERIVED_FROM`, `VARIANT_OF`, `COMPOSED_OF`, `DEPENDS_ON`
- `BindingMode`: `float`, `pin` *(meaningful only on `DEPENDS_ON` edges)*

What each relationship means (full treatment in [`PIPELINE_MODEL.md`](PIPELINE_MODEL.md)):

| RelType | "A —rel→ B" reads as | Example |
|---|---|---|
| `INSTANCE_OF` | A is a live instance of B | a placed barrel ⟶ the master barrel |
| `DERIVED_FROM` | A was forked/baked from B at a version | a normal map ⟶ the high-poly sculpt |
| `VARIANT_OF` | A is a sibling variant of B | "mossy barrel" ⟶ "barrel" |
| `COMPOSED_OF` | A contains/assembles B | a tavern set ⟶ its props |
| `DEPENDS_ON` | A consumes B, `float` or `pin`ned | an animation ⟶ a rig |

`location_uri` is **opaque** to the core — `//depot/...`, `git://...`, `s3://...`,
`/Game/...` are all just strings. Only resolvers (L3) parse them.

---

## 5. The dependency firewall (the thesis as a build gate)

The whole design rests on inward-only dependencies. It is enforced two ways:

```bash
# canonical gate (needs import-linter, in the `dev` extra)
lint-imports
# or without the console script:
python -c "from importlinter.cli import lint_imports; lint_imports()"
```

Three contracts live in `pyproject.toml [tool.importlinter]`:

1. **Inward-only layering** — `service → infra → app → core`.
2. **SDK over HTTP** — `assetcore.sdk` may not import `core`/`app`/`infra`/`service`.
3. **Integrations import only the SDK** — `assetcore.integrations` may not import the inner layers.

A second, zero-dependency backstop runs in the normal suite:
`tests/contract/test_sdk_firewall.py` AST-scans the source so the firewall is
checked even without import-linter installed.

> If a change makes you want to `import maya` inside `core/`, or read a path off
> disk to establish identity, **stop** — you've found a leak. The fix is almost
> always a new tool-agnostic concept (a relationship type, a facet field, a
> resolver), never a branch in the core. See ARCHITECTURE Part 11 for the full
> anti-pattern catalogue.

---

## 6. Testing

```bash
python -m pytest tests/                       # everything
python -m pytest tests/unit/                  # pure rules/verbs — microseconds, no I/O
python -m pytest tests/contract/              # full HTTP stack + adapter contract suite
python -m pytest tests/integration/           # cross-backend workflows, migrations
python -m pytest tests/ -k environment        # filter by name
```

Layout and intent:

| Dir | What it proves | Style |
|---|---|---|
| `tests/unit/` | the rules and verbs in isolation | call `verbs.*` / `rules.*` against `InMemoryRepo` + `InMemorySink` |
| `tests/contract/` | the wire contract + that every adapter behaves identically | drive the real `create_app()` stack through a `TestClient` via `AssetcoreClient` |
| `tests/integration/` | end-to-end workflows on real backends | parametrized across in-memory **and** sqlite (and Postgres when `ASSETCORE_TEST_DSN` is set) |
| `tests/test_scenarios.py` | the protected prototype still works | leave untouched |

The contract suite's shared fixtures (`tests/contract/conftest.py`) give you a
`service` (a `TestClient` over a fresh sqlite app) and a `make_client(token)`
factory — the pattern to copy when writing new contract tests. FastAPI is imported
*inside* those fixtures so a bare install simply skips service tests instead of
erroring.

**Cross-backend Postgres run** (optional): stand up a throwaway PG, then
`ASSETCORE_TEST_DSN=postgresql://... python -m pytest tests/integration/`.

---

## 7. Extending: add a new DCC ("the weekend adapter")

The point of the design: a new authoring tool is a new **contract-tested adapter**
against a stable API, not a core change. The tool-agnostic flow (publish, reference,
the stamp-overwrite guard) lives in `sdk/dcc_adapter.DCCAdapter`; an adapter only
fills in the handful of tool-specific seams.

1. **Create `assetcore/integrations/<tool>.py`** subclassing `DCCAdapter`. Implement
   the seams the base calls — set `tool = "<name>"` and provide the stamp
   read/write and the current location/revision lookups. Keep every tool/COM/CLI
   import **lazy** (inside the method) so the module imports cleanly headless. Model
   it on `integrations/maya.py` / `max.py` / `photoshop.py` — they all share the
   same shape.
2. **Make the seams injectable.** Take the doc/VCS seam objects in `__init__` with
   real defaults; tests pass fakes. This is what lets one parametrized contract
   cover every adapter.
3. **Add it to the DCC contract suite** so it must pass the *same* tests as every
   other adapter (the swap test). If it passes with the core untouched and the
   firewall green, the thesis held.
4. **Prove it live** when the tool is installed — write a `scripts/live_<tool>_*.py`
   driver and follow [`LIVE_PROVING.md`](LIVE_PROVING.md).

The rule of thumb: if onboarding a tool required touching anything but L4 (and maybe
a resolver), that's a leak — find the tool-agnostic concept underneath instead.

---

## 8. Extending: add a storage backend or tracker (a provider)

Swappable services (repo, tracker, source-VCS, runtime-store) go through one
generic registry (`sdk/providers.py`) and are selected by **config**, never by an
import in application code.

```python
from assetcore.sdk import providers

@providers.register("tracker", "myhub", requires=["base_url", "api_key"])
def _build_myhub(config, client):
    return MyHubAdapter(config["base_url"], config["api_key"], client)
```

Then select it in `assetcore.toml` — no caller changes:

```toml
[trackers.production]
provider = "myhub"
config = { base_url = "https://hub.example", api_key = "${MYHUB_KEY}" }
```

`requires=[...]` lets `Settings.validate()` check a studio's config (missing keys,
unset `${ENV}` refs) without knowing any provider's internals. Validate before
deploy:

```bash
MYHUB_KEY=... python -m scripts.validate_config assetcore.toml   # exit 0 / 1
```

Registrations live in `infra/_providers.py` (storage) and
`integrations/_register.py` (trackers); the composition root imports those so the
names exist. Full treatment: [`PROVIDER_LAYER.md`](PROVIDER_LAYER.md).

---

## 9. Authorities & tokens

The L2 service authenticates *who* is calling via the `X-Assetcore-Token` header,
mapping a token to an **authority**. Dev defaults (`service/auth.py`):

| Token | Authority | May call |
|---|---|---|
| `prod-token` | production | `claim`, `rename`, `deprecate` |
| `artist-token` | artist | `bind_source`, `declare`, `bulk_declare` |
| `engine-token` | engine | `bind_runtime`, `declare`, `bulk_declare` |
| `build-token` | build | `bind_runtime` |

`relate` / `set_binding` / `relocate` / `bulk_relate` / `bulk_relocate` need **any**
authenticated authority (the recorded actor is caller-supplied, defaulting to the
authority). **All reads are open** (no token required). Override the token map in
production with `ASSETCORE_TOKENS` (JSON); real RBAC is future hardening.

---

## 10. Coding conventions (the house style)

- **Rules are pure.** Anything in `core/rules.py` takes state and returns a
  decision — no repo, no sink, no I/O. If you're tempted to pass a repo in, the
  logic belongs in `app/verbs.py`.
- **The core never learns a tool.** No tool name, no `import maya`, no URI parsing
  below L4.
- **Never strip a UUID.** Missing identity is recoverable (provisional backfill);
  stripped identity is not.
- **Identity is immutable; facets are mutable.** A rename is an `UPDATE` to one
  facet and never moves files.
- **The binding DB stores pointers, never bytes.**
- **No AI attribution** in commits or PRs — absolute (see `CLAUDE.md`).
- Prefer small composable pure functions over frameworks; match the surrounding
  comment density and naming.

### Git workflow

`main` is the baseline; do feature work on a branch and merge via PR (squash). Push
only when asked. Keep the firewall green and the suite passing in every commit.

---

## 11. Where things live — cheat sheet

| I want to… | Go to |
|---|---|
| change what a verb *means* | `core/rules.py` (the decision) + maybe `app/verbs.py` (the orchestration) |
| add/alter a verb's HTTP shape | `service/routes.py` + `service/schemas.py` |
| add a client method | `sdk/client.py` (then maybe `sdk/cli.py`) |
| onboard a DCC | `integrations/<tool>.py` + the contract suite |
| swap storage / tracker | `assetcore.toml` (+ a provider registration if new) |
| teach the system a new URI scheme | `sdk/resolvers.py` |
| add a reactive recipe | `sdk/automation.py` consumers (register handlers) |
| change the data model | `core/entities.py` + `core/types.py` + `db/schema.sql` + a migration |

For copy-paste usage of every capability above, see **[`COOKBOOK.md`](COOKBOOK.md)**.

---

## 12. Building the docs site

The documentation is a [MkDocs](https://www.mkdocs.org/) site
([Material](https://squidfunk.github.io/mkdocs-material/) theme). The narrative docs
are the Markdown files in `docs/`; the **API Reference is generated from the package
docstrings** by [mkdocstrings](https://mkdocstrings.github.io/) at build time, so it
tracks the code automatically — write a good docstring and it shows up.

```bash
pip install -e ".[docs]"     # or: pip install -r docs/requirements.txt

mkdocs serve                 # live-reload preview at http://127.0.0.1:8000
mkdocs build --strict        # render to ./site (--strict fails on broken links/refs)
```

How it's wired (`mkdocs.yml` at the repo root):

- **`plugins: mkdocstrings`** — the `python` handler statically introspects the
  `assetcore` package (via [griffe](https://mkdocstrings.github.io/griffe/) — no
  imports executed, so lazy tool/COM imports never fire). Private (`_`-prefixed)
  members are hidden.
- **`docs/api/*.md`** — one page per layer, each just a few `::: assetcore.<module>`
  directives. To document a **new module**, add a `::: assetcore.<pkg>.<module>` line
  to the relevant page (and a `nav:` entry if it's a new layer).
- **`toc.slugify`** is set to the GFM-compatible slugifier so in-page anchor links
  resolve identically on GitHub and in the rendered site.

Keep `mkdocs build --strict` green: it's the gate that catches a broken cross-link
or a `:::` pointing at a renamed module. The generated `site/` is git-ignored.

### Continuous build & publish

`.github/workflows/docs.yml` runs `mkdocs build --strict` on **every PR and push**
(the gate), and **deploys to GitHub Pages from `main`** via the Pages-artifact flow
(no `gh-pages` branch to manage). One-time repo setup to turn on hosting: **Settings →
Pages → Build and deployment → Source = "GitHub Actions"**. Until that's set, the
build job still runs and gates PRs; only the deploy step is idle.
