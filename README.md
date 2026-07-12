# assetcore

**Identity-first asset management for game & film production pipelines.**

Most studio "asset management" is really file management — identity is the file
path, so Production, the DCC, and the game engine are forced to agree on one
naming convention and fight over it forever. `assetcore` takes a different
stance:

> An asset is an immutable **identity** (a UUID). Three sovereign **facets** —
> `identity` (Production), `source` (Artist/DCC), `runtime` (engine) — hang off
> it, bound only by that UUID. Each authority owns its facet and is blind to the
> others' names. Renames never move files. Reuse, derivation, and dependency are
> a **graph**, not folders.

The full rationale is in [`docs/DESIGN.md`](docs/DESIGN.md) — **start there.**

For the complete blueprint — empty directory to finished product, the layered
design, the adapter SDK that makes new tools trivial, and the phased build plan —
see **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**. To build on or extend the
codebase, see **[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)**; for copy-paste usage
of every capability, the **[`docs/COOKBOOK.md`](docs/COOKBOOK.md)**. The full doc
map is [`docs/README.md`](docs/README.md).

## Quickstart (zero setup)

```bash
python demo.py            # narrated walkthrough of the 3 real scenarios
python -m pytest tests/   # regression suite
```

Everything runs on in-memory SQLite out of the box — no database to install.
Postgres is the production target (`assetcore/db/schema.sql` is Postgres dialect;
the SQLite backend translates it on the fly).

## What it solves (the three scenarios in `tests/`)

- **The barrel** — environment artists reuse the *same* asset across sets via a
  live relationship instead of copy-pasting a new barrel every time; lineage is
  recorded, so "where is this used / where did this come from" are graph queries.
- **Robin's locomotion** — animations shared live from Batman (`INSTANCE_OF`),
  forked with lineage (`DERIVED_FROM`), or unique — three reuse semantics, one
  mechanism. "What breaks if I fix Batman's walk?" is answerable.
- **The materials bottleneck** — a downstream animator *floats* a dependency to
  get upstream material updates for free (no model→rig→anim republish chain),
  then *pins* it before delivery. One column (`binding_mode`) does it.

## The universal verbs

Identity & facets: `declare` · `claim` · `rename` · `bind_source` · `bind_runtime` ·
`relocate` · `deprecate` · `resolve`. Graph: `relate` · `set_binding` ·
`resolve_dependency` · `used_by` · `lineage` · `dependents`/`dependencies` (transitive)
· `stale_derivations` · `floating_dependencies`. Human surfaces & scale:
`find_similar` · `backfill_worklist` · `bulk_declare`/`relate`/`relocate`. Plus a
live **event spine** (SSE). Every one is shown three ways in the
[**Cookbook**](docs/COOKBOOK.md).

The API is the only door — everything traffics in UUIDs, never paths.

## Layout

```
assetcore/        the layered framework — core / app / infra / service / sdk / integrations
docs/             the documents (start: docs/README.md → DESIGN → ARCHITECTURE → DEVELOPMENT → COOKBOOK)
tests/            unit / contract / integration suites + the protected prototype scenarios
scripts/          operational drivers (live_* drive real tools; demo_* are narrated)
demo.py           narrated end-to-end run
CLAUDE.md         context for continuing with Claude Code
```

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#3-repository-layout--the-layers) for
the per-layer breakdown.

## Studio UGS hub bridge (`feature/ugs-hub`)

Python SDK + CLI for the **Studio UGS + asset-core** artist hub: hybrid local-first
reads, central-or-outbox writes, config-driven DCC launch. Design spec lives in the
companion `ugs-dev` repo (`docs/superpowers/specs/2026-07-11-ugs-assetcore-hub-design.md`).

### Hybrid model (summary)

| Component | Role |
|-----------|------|
| Local replica | `%LOCALAPPDATA%/StudioUGS/cache/assetcore.db` — hydrated on UGS sync |
| Local reader | `assetcore hub serve-local` on `127.0.0.1:8741` (identity `/health`) |
| Outbox | `%LOCALAPPDATA%/StudioUGS/cache/outbox.db` — WAL sqlite queue when central is down |
| `HybridClient` | Reads: local reader first, central fallback (same response shape either way). Writes: central if up, else outbox + optimistic replica patch |

Launch and UE editor start **never** require central. See spec §5.4 availability matrix.

### `assetcore hub` CLI

All verbs emit JSON on stdout; exit `0` on success, `1` on failure (except `serve-local`, which blocks).

| Verb | Purpose |
|------|---------|
| `hub launch <tool_id> --context <json>` | Spawn DCC from `pipeline.toml` launch target |
| `hub hydrate --context <json>` | Pull user assets + dependency closure into local replica |
| `hub serve-local --config <toml>` | Start local HTTP reader (blocking) |
| `hub sync-outbox --config <toml>` | Replay pending outbox entries to central |
| `hub retry-failed --config <toml>` | Reset failed → pending and replay |
| `hub health --config <toml>` | `{central, local_reader, outbox_pending, outbox_failed}` |
| `hub list-targets --config <toml>` | `[{id, label, icon, available, reason, executable_path}]` for UGS UI |

Example studio config: [`pipeline.toml.example`](pipeline.toml.example) (6 DCC launch targets, `${VAR}` expansion per spec §6.1).

Install: `pip install -e .` on branch `feature/ugs-hub`. UGS plugin invokes `assetcore` via subprocess only — no Python in the C# host.

## Continuing with Claude Code

This repo is set up for it: `CLAUDE.md` carries the project context and hard
rules, `docs/DESIGN.md` carries the full rationale, and `docs/ROADMAP.md` lists
the next high-value tasks (FastAPI service, event subscriber, fleshing out the
Maya/Unreal stamping). Open the folder in Claude Code and it'll pick up the
context automatically.

## License

MIT (see LICENSE).
