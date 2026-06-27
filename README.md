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

This is an early exploration/prototype. The full rationale is in
[`docs/DESIGN.md`](docs/DESIGN.md) — **start there.**

For the complete blueprint — empty directory to finished product, the layered
design, the adapter SDK that makes new tools trivial, and the phased build plan —
see **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**.

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

## The core verbs (`assetcore/api.py`)

`declare` · `claim` · `rename` · `bind_source` · `bind_runtime` · `relate` ·
`resolve` · `resolve_dependency` · `used_by` · `lineage`

The API is the only door — everything traffics in UUIDs, never paths.

## Layout

```
assetcore/        the framework (api + db + integration stubs)
docs/             DESIGN.md (read first) + ROADMAP.md
tests/            the scenarios + invariants as pytest
demo.py           narrated end-to-end run
CLAUDE.md         context for continuing with Claude Code
```

## Continuing with Claude Code

This repo is set up for it: `CLAUDE.md` carries the project context and hard
rules, `docs/DESIGN.md` carries the full rationale, and `docs/ROADMAP.md` lists
the next high-value tasks (FastAPI service, event subscriber, fleshing out the
Maya/Unreal stamping). Open the folder in Claude Code and it'll pick up the
context automatically.

## License

MIT (see LICENSE).
