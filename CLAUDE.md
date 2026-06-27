# CLAUDE.md — context for Claude Code

Read automatically by Claude Code. Orients you on what this project is, the
non-negotiable principles, the build plan, and how to extend without breaking the
core idea.

## Read these first, in order

1. **`docs/ARCHITECTURE.md`** — THE BLUEPRINT. Empty directory -> finished product:
   the layered design, the universal verbs, the adapter SDK, the build phases, the
   contract-testing approach, and the anti-patterns that kill the design. This is
   what you are building.
2. **`docs/DESIGN.md`** — the *why*: the real production pain points and the core
   idea, with the three scenarios worked through.
3. **`docs/ROADMAP.md`** — phase/task tracking.

The current code (`assetcore/api.py`, `db/schema.sql`, `tests/`) is the **working
prototype seed**. ARCHITECTURE.md Appendix A explains exactly how it maps onto the
layered target — Phase 1 starts from this code, not a blank page.

## The thesis (the entire point of the project)

**The core knows nothing about any tool. Tools know how to speak to the core.**

Swapping Maya for Blender, or adding Houdini/Substance, must be a *weekend* — a new
contract-tested adapter against a stable API — never a core change. If onboarding a
tool requires touching anything but the integration layer (L4) and maybe a
resolver, that's the loudest possible alarm: a leak. Stop and find the
tool-agnostic concept underneath (usually a new relationship type or facet field),
add THAT, never a branch.

## The one idea underneath (the data model)

An asset is an immutable IDENTITY (UUID). Three sovereign FACETS hang off it:
identity (Production), source (DCC), runtime (engine), bound only by the UUID.
Nothing is inferred; each authority writes only its own facet. A rename is an
UPDATE to one facet and never moves files.

## The layered architecture (dependencies point INWARD only)

```
L4 integrations  (disposable: maya/blender/unreal/houdini/substance/shotgrid)
L3 sdk           (the contract: AssetcoreClient + DCC/Engine/Tracker adapter bases)
L2 service       (the only door: FastAPI + auth-per-authority + event fan-out)
L1 app           (the verbs: declare/claim/bind_*/relate/resolve/...)
L0 core          (pure domain: entities + rules + ports. NO I/O, NO tool names)
```

core imports nothing internal. integrations import only sdk. This is CI-enforced
by import-linter (ARCHITECTURE Part 8) — `import maya` inside core/ fails the
build. The thesis is a build gate, not a guideline.

## Hard rules (violating any is a design regression)

1. The API is the only door. Nothing reads a path off disk to establish identity.
2. The core never learns a tool. No tool name below L4. No parsing a location_uri
   in the core — URIs are opaque; resolvers parse them.
3. Never strip a UUID. Missing identity is recoverable (provisional backfill);
   stripped identity is not.
4. Identity is immutable; facets are mutable. A rename touches one facet only.
5. Rules live in L0/L1. Never in a route handler (L2) or a tool hook (L4).
6. The binding DB stores pointers, never bytes.

Full anti-pattern catalogue: ARCHITECTURE Part 11.

## Build order (each phase ends RUNNABLE — see ARCHITECTURE Part 9)

1. Pure core + InMemoryRepo -> scenarios pass with no DB
2. Verbs + Sqlite/Postgres repos -> same tests green on all backends
3. FastAPI service + CLI + live event subscribe -> nervous system real, no tools yet
4. Adapter SDK + contract test suite + FakeDCCAdapter passes it
5. Real Maya + Unreal + Perforce resolver -> barrel round-trips, "Open Source" works
6. The swap test: Blender + Substance pass the SAME contract suite, core untouched
7. ShotGrid as a view + backfill worklist + dedupe nudge + validation gates
8. Hardening: stamp-coverage CI gate, idempotent events, observability, backup

## How to run the current prototype

```bash
python demo.py            # narrated walkthrough of the 3 scenarios
python -m pytest tests/   # the suite (in-memory SQLite, zero setup)
```

## Tone for working here

The author is a senior pipeline engineer who cares about the design being a
*natural* solution, not a pile of special cases. Be concrete, show running code,
prefer small composable pure functions over frameworks, and call out any moment a
change risks eroding the "core knows no tools" or "identity is not the path"
guarantees. When in doubt, protect the boundary.
