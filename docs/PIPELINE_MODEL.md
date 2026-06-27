# Pipeline Model — disciplines, relationships, and the capability gaps

Grounds the asset-management model in real game-production practice across every
discipline, so the relationship graph and the verbs match how studios actually
work. Synthesized from research into Concept, Modeling, Sculpting, Retopo, UV,
Texturing/Baking, Materials, LookDev, Rigging, Skinning, Animation, Environment/
Hard-surface, and FX pipelines (Maya, 3ds Max, ZBrush, Substance, Houdini,
Photoshop, Unreal). It drives the next build phases (the core gaps, then the
environment + animation proving workflows, then the agnostic toolset).

## The pipeline as a dependency graph

```
Concept ─DEPENDS_ON──────────────────────────────────────────────┐ (everything starts here)
  │
  ├─► Modeling (base/blockout) ──DERIVED_FROM──► Hi-poly Sculpt ──DERIVED_FROM──► Retopo (low-poly + LODs)
  │            └─ (master base meshes reused: humanoid male/female → cast)            │
  │                                                                                   ├─ UV (mesh data; DEPENDS_ON mesh)
  │                                                                                   ▼
  │   Bake maps ─DERIVED_FROM─ {hi-poly + low-poly + UVs}  (two/three-parent derive; stale if ANY changes)
  │                                                                                   ▼
  │   Texturing (.spp → texture SET = COMPOSED_OF maps) ─DERIVED_FROM─ bakes; DEPENDS_ON smart-materials
  │                                                                                   ▼
  │   Master Material ◄─INSTANCE_OF─ Material Instances (name-keyed param overrides; live downward propagation)
  │                                                                                   ▼
  │   LookDev (COMPOSED_OF pointers to mesh+textures+materials+lighting; approval tied to versions)
  │
  ├─► Rigging (skeleton + control rig) ──DEPENDS_ON──► mesh;  shared skeleton INSTANCE_OF master rig
  │        ▼
  │   Skinning/Weights ──DEPENDS_ON── {mesh + skeleton}   (keyed by joint name + vert order — fragile)
  │        ▼
  │   Animation clips ──DEPENDS_ON── skeleton;  retargeted clip ─DERIVED_FROM─ source clip
  │        ▼
  │   Locomotion/Anim Sets ──COMPOSED_OF── clips   (MANY-TO-MANY: one walk in many sets)
  │
  └─► Environment: Kit ─COMPOSED_OF─ pieces (each DEPENDS_ON shared trim/master material)
           ▼  props (VARIANT_OF base; mossy/clean/damaged)
       Set Dressing ─COMPOSED_OF─ INSTANCE_OF(masters)   (procedural scatter vs hand-placed)
           ▼
       Level / World ─COMPOSED_OF─ Level Instances/PLAs (recursive: city→district→block→kit→piece)
                      └─ DEPENDS_ON masters it does not contain

FX: Niagara System ─COMPOSED_OF─ emitters (INSTANCE_OF parent emitter); VAT/flipbook ─DERIVED_FROM─ Houdini sim
```

## Relationship vocabulary, mapped to reality

The existing `RelType` set is sufficient — every real handoff classifies cleanly:

| RelType | Means | Canonical examples |
|---|---|---|
| `COMPOSED_OF` | an assembly references its parts (many-to-many, shared parts) | locomotion set→clips; level→instances; kit→pieces; texture set→maps; Niagara system→emitters |
| `DEPENDS_ON` | needs at author/runtime but doesn't contain or derive from | clip→skeleton; mesh→material; weights→{mesh,skeleton}; level→masters |
| `DERIVED_FROM` | generated/baked/forked from a source; **stale if source advances** | low-poly→hi-poly; bake→{hi,low,UV}; retarget→source clip; texture export→.spp |
| `INSTANCE_OF` | a placed/parametric copy of a master | level instance→master; material instance→master material; emitter→parent emitter |
| `VARIANT_OF` | an alternate of a master | mossy/clean barrel; root-motion vs in-place clip; per-character locomotion set |

## The hard realities the system must handle (research consensus)

1. **Master-update propagation with selective version pinning.** One master is
   instanced across hundreds of consumers; an edit must reach floating consumers
   while shipped/locked ones pin an exact version. **Already supported** by per-edge
   `FLOAT`/`PIN` + `resolve_dependency`. The gap is doing it *transitively*.
2. **Transitive blast radius.** "What breaks if I rename/retire/change X?" spans
   deep graphs (joint→weights+clips+sets+AnimBP; master material→instances→meshes→
   levels; city→…→prop). `used_by`/`lineage` are **one-hop** today → **gap**.
3. **Derivation staleness.** A bake is `DERIVED_FROM` {hi-poly, low-poly, UVs}; if
   any advances, the bake — and all hand-paint layered on it — is stale. The derive
   edge must record the source version it was derived at → **gap**.
4. **Rename/relocate at directory scale without orphaning.** IP/legal renames and
   art reorgs move hundreds–thousands of files; identity (UUID) must survive while
   the location facet updates. `rename` (identity) exists; **location relocate +
   batch is a gap** (today `bind_source` makes a content *version*, wrong for a move).
5. **Load-bearing provenance that never ships.** Hi-poly, `.spp`/`.sbs`/`.hip`,
   sim caches, concept `.psd` define correctness but aren't runtime assets. The
   `DERIVED_FROM`/`DEPENDS_ON` edge must persist for source-only identities (it does
   — facets are independent; an asset can have a source facet and no runtime facet).
6. **Many-to-many shared membership.** One walk cycle is a member of many sets; one
   trim sheet textures a whole kit. `COMPOSED_OF` over shared, independently-versioned
   parts — already expressible; transitive queries (#2) make it actionable.
7. **Name-keyed override orphaning (engine-specific).** UE material-instance and
   Niagara overrides bind by *parameter name*; renaming a parent param silently
   reverts descendants. This is an **L4/validation** concern (detect + event), not
   core — surfaced by a validation gate, not a new verb.

## Capability gaps → where they live

**Core (L0/L1) — the next phase:**
- **Transitive graph queries** — `dependents(asset, rel_types?, depth?)` and
  `dependencies(...)` closures (powers impact, retire-safety, propagation views).
- **Derivation staleness** — record the source version on a `DERIVED_FROM` edge;
  `stale_derivations(asset)` flags derived assets whose source advanced.
- **Relocate** — a verb that updates a facet's `location_uri` in place (same
  identity, NOT a content version) + **batch relocate** for directory moves.
- **Deprecate/retire** — set `Lifecycle.DEPRECATED` + list live consumers (uses
  transitive queries) so retiring is safe.
- **Bulk operations** — batch declare/relate/relocate/resolve for the 100s-scale.

**Adapter (L4) / validation:**
- Name-keyed override orphaning detection (UE material/Niagara) as a validation
  gate + event; not a core change.

**Tool (L3+) — the toolset phase:**
- `assetcore` CLI; rename/relocate tool with impact preview; reuse browser; event
  automation. (See the toolset scope in the conversation plan.)

## Concept Art + Photoshop (front of the pipeline)

Concept is the **first** discipline — every asset ultimately `DEPENDS_ON` (or is
`DERIVED_FROM`) a concept. It's a normal **source-authoring** discipline:
- **Assets:** concept paintings, paintovers, callout/turnaround sheets, color keys,
  style guides (`.psd`); style guides are reused (a shared reference many assets
  depend on).
- **Relationships:** model `DEPENDS_ON`/`DERIVED_FROM` its concept; concept variants
  are `VARIANT_OF`; a callout sheet is `COMPOSED_OF` reference crops.
- **Provenance:** the `.psd` is load-bearing but rarely ships (reality #5).

**`integrations/photoshop.py` — a new DCC adapter** (the "weekend adapter" again):
a `DCCAdapter` whose scene seam stamps identity into the `.psd` **XMP/File-Info**
metadata (the Photoshop analog of Maya's `fileInfo` / Max's `fileProperties`) and
binds source via the same Perforce seam. Headless via Photoshop COM automation
(win32com / `photoshop-python-api`). Because the seam shape matches the others, it
passes the **identical DCC contract** — no change below L4. Build it against a fake
now; prove it live once Photoshop finishes installing (same pattern as Maya/Max).

## Proving plan (grounded)

1. **Core gaps** (above) — pure L0/L1, contract-tested, firewall-clean.
2. **Environment workflow first** (then animation): a kit of 100s → reuse across
   many levels → instancing/variants → env-of-envs (transitive impact) → an art-reorg
   directory **relocate** → rename-for-IP, identity stable throughout. Animation:
   loco sets, shared/forked clips, fix-propagates, Batman→Nightwing rename.
3. **Agnostic toolset** — `assetcore` CLI backbone first, then the production
   rename/relocate tool (impact preview), artist reuse/open-source, event automation.
4. **Concept/Photoshop** folds in as a source adapter + concept→asset relationships.
