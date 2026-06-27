# assetcore docs

The map. Read top-to-bottom if you're new; jump by need otherwise.

## Start here (the *why* and the blueprint)
- **[DESIGN.md](DESIGN.md)** — the production pain, the core idea, three worked scenarios. Read first.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — empty directory → finished product: the layers, the universal verbs, the adapter SDK, the firewall, the anti-patterns.

## Build & use
- **[DEVELOPMENT.md](DEVELOPMENT.md)** — set up, run, test, the layout, the firewall, and how to extend (new DCC, new provider) without leaking the boundary.
- **[COOKBOOK.md](COOKBOOK.md)** — copy-paste examples for **every capability**, three surfaces (in-process / SDK-HTTP / CLI-curl).
- **[CLI.md](CLI.md)** — the `assetcore` command reference.

## Reference
- **[PIPELINE_MODEL.md](PIPELINE_MODEL.md)** — disciplines (modeling, rigging, anim, FX, environment, concept art…) and which relationship to use when.
- **[PROVIDER_LAYER.md](PROVIDER_LAYER.md)** — config-driven backend/tracker swaps and how to write a provider.
- **[OPERATIONS.md](OPERATIONS.md)** — the rollout runbook: firewall gate, migrations, metrics, stamp-coverage gate, backup/restore.
- **[LIVE_PROVING.md](LIVE_PROVING.md)** — driving real Maya / 3ds Max / Unreal / ShotGrid / Photoshop end-to-end.

## Project
- **[ROADMAP.md](ROADMAP.md)** — phase/task tracking.
- **[PHASE1.md](PHASE1.md)** — the Phase-1 build notes (historical).

## The one-line model
> An asset is an immutable **identity** (UUID). Three sovereign **facets** —
> identity (Production), source (Artist/DCC), runtime (engine) — hang off it, bound
> only by the UUID. Renames never move files. Reuse/derivation/dependency are a
> graph. **The core knows no tools; tools know how to speak to the core.**
