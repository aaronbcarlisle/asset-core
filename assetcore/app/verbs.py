"""The universal verbs (L1), ported from the prototype api.py.

Each verb takes the repo + event sink (injected — the ports, not concrete
backends), uses core.rules for every decision, persists through the repo, and
emits an Event. Same names and behavior as api.py; the tool-specific params are
gone (depot_path/dcc/p4_changelist -> location_uri/tool/revision), and the two
concerns api.py mixed — pure logic and persistence — now live apart (rules vs
here). Depends on `core` only; knows nothing of SQLite, HTTP, or any tool.
"""
from uuid import UUID

from assetcore.core import rules
from assetcore.core.entities import (
    Asset,
    Event,
    IdentityFacet,
    Relationship,
    RuntimeVersion,
    SourceVersion,
)
from assetcore.core.ports import AssetRepo, EventSink
from assetcore.core.types import BindingMode, Lifecycle, RelType


# ---------------------------------------------------------------------------
# DECLARE — summon an asset into existence; returns a durable provisional UUID.
# ---------------------------------------------------------------------------
def declare(repo: AssetRepo, sink: EventSink, asset_type: str, created_by: str,
            origin: dict | None = None) -> UUID:
    """Summon an asset into existence; return its durable provisional UUID.

    The asset is born PROVISIONAL with an empty identity facet already attached
    (ready for backfill). `origin` records free-form birth context (shot, dcc, …)
    that `find_similar` and the worklist use later. Emits a ``declared`` event.
    """
    asset = Asset(asset_type=asset_type, created_by=created_by, origin=origin or {})
    identity = IdentityFacet(asset_id=asset.id)   # facet exists from birth, for backfill
    repo.create_asset(asset, identity)
    sink.emit(Event(asset.id, "declared", {"asset_type": asset_type}, created_by))
    return asset.id


# ---------------------------------------------------------------------------
# CLAIM — Production gives a provisional asset meaning (the backfill step).
# ---------------------------------------------------------------------------
def claim(repo: AssetRepo, sink: EventSink, asset_id: UUID, display_name: str,
          taxonomy: str, actor: str, **attrs) -> None:
    """Production gives a provisional asset meaning — the backfill step.

    Sets the identity facet's display name + taxonomy and flips lifecycle to
    ACTIVE. ``**attrs`` is an authoritative set of identity attributes (a claim
    with none clears them). Raises ``ValueError`` if the asset is unknown. Emits
    an ``identity.claimed`` event.
    """
    identity = repo.get_identity(asset_id)
    if identity is None:
        raise ValueError(f"cannot claim unknown asset {asset_id}")
    identity.display_name = display_name
    identity.taxonomy = taxonomy
    identity.attributes = dict(attrs)   # authoritative set: a claim with no attrs clears them
    repo.save_identity(identity)
    repo.set_lifecycle(asset_id, Lifecycle.ACTIVE)
    sink.emit(Event(asset_id, "identity.claimed", {"name": display_name}, actor))


# ---------------------------------------------------------------------------
# RENAME — relabel the identity facet ONLY. No file moves, no engine changes.
# ---------------------------------------------------------------------------
def rename(repo: AssetRepo, sink: EventSink, asset_id: UUID, new_name: str,
           actor: str, new_taxonomy: str | None = None) -> None:
    """Relabel the identity facet ONLY — no file moves, no engine changes.

    The headline guarantee that identity is not the path: a rename touches one
    facet. Pass ``new_taxonomy`` to also re-file it taxonomically (still no bytes
    move). Raises ``ValueError`` if unknown. Emits an ``identity.renamed`` event.
    To move the bytes instead, see :func:`relocate`.
    """
    identity = repo.get_identity(asset_id)
    if identity is None:
        raise ValueError(f"cannot rename unknown asset {asset_id}")
    identity.display_name = new_name
    if new_taxonomy is not None:
        identity.taxonomy = new_taxonomy
    repo.save_identity(identity)
    sink.emit(Event(asset_id, "identity.renamed", {"name": new_name}, actor))


# ---------------------------------------------------------------------------
# BIND_SOURCE — artist/DCC publishes authored truth. Writes the source facet only.
# ---------------------------------------------------------------------------
def bind_source(repo: AssetRepo, sink: EventSink, asset_id: UUID, location_uri: str,
                tool: str, revision: str, published_by: str) -> int:
    """The artist/DCC publishes authored truth — write the SOURCE facet.

    Adds a new source version (a pointer; ``location_uri`` is opaque, ``revision``
    a string) and returns its monotonic version number. The prior latest is
    demoted as part of the same write (the ``one_latest_source`` invariant). Emits
    a ``source.published`` event.
    """
    v = rules.next_version_num(repo.source_versions(asset_id))
    # The repo demotes the prior latest as part of the write (the schema's
    # one_latest_source unique index forces demote-then-insert atomically).
    repo.add_source_version(SourceVersion(
        asset_id=asset_id, location_uri=location_uri, tool=tool,
        revision=str(revision), version_num=v, is_latest=True, published_by=published_by,
    ))
    sink.emit(Event(asset_id, "source.published",
                    {"location_uri": location_uri, "version": v, "tool": tool}, published_by))
    return v


# ---------------------------------------------------------------------------
# BIND_RUNTIME — the build/engine reports where the cooked asset lives.
# ---------------------------------------------------------------------------
def bind_runtime(repo: AssetRepo, sink: EventSink, asset_id: UUID, location_uri: str,
                 build_id: str) -> int:
    """The build/engine reports where the cooked asset lives — write RUNTIME.

    Adds a new runtime version and returns its version number; the prior latest is
    demoted at write time (the ``one_latest_runtime`` invariant). Emits a
    ``runtime.cooked`` event.
    """
    v = rules.next_version_num(repo.runtime_versions(asset_id))
    # one_latest_runtime invariant is enforced at write time by the repo.
    repo.add_runtime_version(RuntimeVersion(
        asset_id=asset_id, location_uri=location_uri, build_id=build_id,
        version_num=v, is_latest=True,
    ))
    sink.emit(Event(asset_id, "runtime.cooked",
                    {"location_uri": location_uri, "version": v}, "build"))
    return v


# ---------------------------------------------------------------------------
# RELATE — assert a NEW typed edge. (Flipping an existing edge is set_binding.)
# ---------------------------------------------------------------------------
def relate(repo: AssetRepo, sink: EventSink, frm: UUID, to: UUID, rel_type: RelType,
           actor: str, binding_mode: BindingMode | None = None,
           pinned_version: int | None = None) -> None:
    """Assert a NEW typed edge ``frm -> to``.

    ``binding_mode``/``pinned_version`` are valid only on DEPENDS_ON. For
    DERIVED_FROM the edge records the parent's current source version, so
    :func:`stale_derivations` can flag it once the parent advances. The edge is
    validated (self-edges and a binding_mode on a non-DEPENDS_ON edge raise
    ``ValueError``). Emits a ``relationship.added`` event. Flipping an existing
    edge float↔pin is :func:`set_binding`, not this.
    """
    rel_type = RelType(rel_type)
    if binding_mode is not None:
        binding_mode = BindingMode(binding_mode)
    attributes: dict = {}
    if rel_type == RelType.DERIVED_FROM:
        # anchor staleness: record the source version this child was derived at, so
        # stale_derivations can flag it once the parent's source advances past it.
        parent_latest = next((v for v in repo.source_versions(to) if v.is_latest), None)
        if parent_latest is not None:
            attributes["derived_at_version"] = parent_latest.version_num
    r = Relationship(from_asset=frm, to_asset=to, rel_type=rel_type,
                     binding_mode=binding_mode, pinned_version=pinned_version,
                     attributes=attributes)
    rules.validate_relationship(r)
    repo.add_relationship(r)
    sink.emit(Event(frm, "relationship.added",
                    {"to": str(to), "rel_type": rel_type.value,
                     "binding_mode": binding_mode.value if binding_mode is not None else None}, actor))


# ---------------------------------------------------------------------------
# SET_BINDING — flip an existing DEPENDS_ON edge float<->pin (consumer's call).
# ---------------------------------------------------------------------------
def set_binding(repo: AssetRepo, sink: EventSink, frm: UUID, to: UUID,
                binding_mode: BindingMode, pinned_version: int | None = None) -> None:
    """Flip an EXISTING DEPENDS_ON edge between float and pin (the consumer's call).

    ``float`` always resolves to the latest authored version; ``pin`` locks to a
    specific one. Raises ``ValueError`` if there's no such edge (use :func:`relate`
    to create one). Emits a ``binding.changed`` event.
    """
    binding_mode = BindingMode(binding_mode)
    edge = repo.get_edge(frm, to, RelType.DEPENDS_ON)
    if edge is None:   # set_binding FLIPS an existing edge; it never creates one (relate does)
        raise ValueError(f"no DEPENDS_ON edge {frm} -> {to} to set binding on")
    r = Relationship(from_asset=frm, to_asset=to, rel_type=RelType.DEPENDS_ON,
                     binding_mode=binding_mode, pinned_version=pinned_version,
                     attributes=edge.attributes)   # preserve any edge metadata
    rules.validate_relationship(r)
    repo.upsert_relationship(r)
    sink.emit(Event(frm, "binding.changed",
                    {"to": str(to), "binding_mode": binding_mode.value,
                     "pinned_version": pinned_version}, "consumer"))


# ---------------------------------------------------------------------------
# RESOLVE — UUID -> all three facets (the single lookup that replaces the dig).
# ---------------------------------------------------------------------------
def resolve(repo: AssetRepo, asset_id: UUID) -> dict:
    """UUID -> all three facets in one lookup (the read that replaces the dig).

    Returns a dict with ``meta`` (the Asset), ``identity``, the latest ``source``
    version, and the latest ``runtime`` version. Any facet may be ``None``.
    """
    source = next((v for v in repo.source_versions(asset_id) if v.is_latest), None)
    runtime = next((v for v in repo.runtime_versions(asset_id) if v.is_latest), None)
    return {
        "id": asset_id,
        "meta": repo.get_asset(asset_id),
        "identity": repo.get_identity(asset_id),
        "source": source,
        "runtime": runtime,
    }


# ---------------------------------------------------------------------------
# RESOLVE_DEPENDENCY — a DEPENDS_ON edge -> the exact source version to load.
# ---------------------------------------------------------------------------
def resolve_dependency(repo: AssetRepo, frm: UUID, to: UUID) -> SourceVersion | None:
    """A DEPENDS_ON edge -> the exact source version the consumer should load.

    The pin if the edge is pinned, else the current latest. Returns ``None`` if
    there's no such edge or no matching version. The float/pin decision itself is
    the pure :func:`assetcore.core.rules.resolve_dependency_version`.
    """
    edge = repo.get_edge(frm, to, RelType.DEPENDS_ON)
    if edge is None:
        return None
    return rules.resolve_dependency_version(edge, repo.source_versions(to))


# ---------------------------------------------------------------------------
# LINEAGE / USAGE — graph traversals.
# ---------------------------------------------------------------------------
_USED_BY_TYPES = {RelType.INSTANCE_OF, RelType.COMPOSED_OF, RelType.DERIVED_FROM}
_LINEAGE_TYPES = {RelType.INSTANCE_OF, RelType.DERIVED_FROM, RelType.VARIANT_OF}


def used_by(repo: AssetRepo, asset_id: UUID) -> list[Relationship]:
    """Who instances/composes/derives-from this asset? (where is this used)"""
    return [e for e in repo.edges_to(asset_id) if e.rel_type in _USED_BY_TYPES]


def lineage(repo: AssetRepo, asset_id: UUID) -> list[Relationship]:
    """What is this derived from / instancing? (where did this come from)"""
    return [e for e in repo.edges_from(asset_id) if e.rel_type in _LINEAGE_TYPES]


# ---------------------------------------------------------------------------
# FIND_SIMILAR — the reuse-over-rebuild nudge at declare time (advisory only).
# ---------------------------------------------------------------------------
def find_similar(repo: AssetRepo, name: str, asset_type: str | None = None,
                 limit: int = 10) -> list[tuple]:
    """Rank existing assets that look like `name` so a human can reuse, not rebuild.

    Returns (asset, identity, score) descending by score. Never auto-merges or
    infers identity — the artist still chooses to reuse (relate the existing UUID)
    or declare new. Anti-pattern #5 stays respected.

    Note: this does one get_identity per candidate (an N+1 on SQL backends). It is
    an interactive, type-scoped, advisory nudge over a single asset_type, so the
    candidate set is small; a batched list-with-identity port method is a future
    optimization, not a correctness issue (see PR #7 review thread).
    """
    scored = []
    for asset in repo.list_assets(asset_type=asset_type):
        identity = repo.get_identity(asset.id)
        score = rules.similarity_score(name, asset, identity)
        if score > 0:
            scored.append((asset, identity, score))
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# BACKFILL_WORKLIST — the provisional queue Production grooms (oldest first).
# ---------------------------------------------------------------------------
def backfill_worklist(repo: AssetRepo) -> list[tuple]:
    """Provisional assets awaiting a claim, with their birth context. (asset, identity)."""
    provisional = repo.list_assets(lifecycle=Lifecycle.PROVISIONAL)
    provisional.sort(key=lambda a: a.created_at)          # oldest first: groom the tail
    return [(a, repo.get_identity(a.id)) for a in provisional]


# ---------------------------------------------------------------------------
# FLOATING_DEPENDENCIES — the float footgun guard before delivery.
# ---------------------------------------------------------------------------
def floating_dependencies(repo: AssetRepo, asset_id: UUID) -> list[Relationship]:
    """The consumer's DEPENDS_ON edges still floating — pin these before ship."""
    return rules.floating_dependencies(repo.edges_from(asset_id, RelType.DEPENDS_ON))


# ---------------------------------------------------------------------------
# DEPENDENTS / DEPENDENCIES — transitive graph closures (the impact brain).
# ---------------------------------------------------------------------------
def _as_reltypes(rel_types) -> set[RelType] | None:
    # None -> no filter (traverse all edge types); an explicit empty list -> match
    # nothing (don't silently widen an empty filter to "everything").
    return None if rel_types is None else {RelType(rt) for rt in rel_types}


def dependents(repo: AssetRepo, asset_id: UUID, rel_types=None,
               max_depth: int | None = None) -> list[tuple[UUID, int, RelType]]:
    """Everything that (transitively) depends on this asset — "what breaks if I
    change/rename/retire it". Walks edges UP (edges_to). Returns (asset_id, depth,
    rel_type) in BFS order. `rel_types` filters which edge kinds to traverse.
    """
    want = _as_reltypes(rel_types)

    def neighbors(node):
        return [(e.from_asset, e.rel_type) for e in repo.edges_to(node)
                if want is None or e.rel_type in want]

    return rules.walk_closure(asset_id, neighbors, max_depth)


def dependencies(repo: AssetRepo, asset_id: UUID, rel_types=None,
                 max_depth: int | None = None) -> list[tuple[UUID, int, RelType]]:
    """Everything this asset (transitively) depends on / is built from. Walks edges
    DOWN (edges_from). Returns (asset_id, depth, rel_type) in BFS order.
    """
    want = _as_reltypes(rel_types)

    def neighbors(node):
        return [(e.to_asset, e.rel_type) for e in repo.edges_from(node)
                if want is None or e.rel_type in want]

    return rules.walk_closure(asset_id, neighbors, max_depth)


# ---------------------------------------------------------------------------
# RELOCATE — move/rename the BYTES (location), not the identity or the content.
# ---------------------------------------------------------------------------
def relocate(repo: AssetRepo, sink: EventSink, asset_id: UUID, new_location_uri: str,
             actor: str, facet: str = "source", new_revision: str | None = None) -> None:
    """Update a facet's location in place — a `p4 move` / directory reorg, not a
    new authored version. Identity and every relationship are untouched (the whole
    point of UUID-not-path). `facet` is 'source' or 'runtime'.
    """
    if facet == "source":
        ok = repo.update_source_location(asset_id, new_location_uri, new_revision)
        event = "source.relocated"
    elif facet == "runtime":
        ok = repo.update_runtime_location(asset_id, new_location_uri)
        event = "runtime.relocated"
    else:
        raise ValueError(f"unknown facet {facet!r}; expected 'source' or 'runtime'")
    if not ok:
        raise ValueError(f"no {facet} facet to relocate for {asset_id}")
    sink.emit(Event(asset_id, event, {"location_uri": new_location_uri, "facet": facet}, actor))


# ---------------------------------------------------------------------------
# DEPRECATE — retire an identity (lifecycle only). Check dependents first.
# ---------------------------------------------------------------------------
def deprecate(repo: AssetRepo, sink: EventSink, asset_id: UUID, actor: str) -> None:
    """Mark an identity DEPRECATED. Reversible (it's a lifecycle flag, not a delete)
    and never strips facets or edges — `dependents` still finds who's on it, so a
    retire is safe and auditable.
    """
    if repo.get_asset(asset_id) is None:
        raise ValueError(f"cannot deprecate unknown asset {asset_id}")
    repo.set_lifecycle(asset_id, Lifecycle.DEPRECATED)
    sink.emit(Event(asset_id, "identity.deprecated", {}, actor))


# ---------------------------------------------------------------------------
# STALE_DERIVATIONS — DERIVED_FROM children whose source advanced (re-bake needed).
# ---------------------------------------------------------------------------
def stale_derivations(repo: AssetRepo, asset_id: UUID) -> list[Relationship]:
    """This asset's outgoing DERIVED_FROM edges whose parent source has advanced
    past the version it was derived at — e.g. a bake whose high-poly was re-sculpted.
    Advisory: it flags what to re-derive, it never auto-rebuilds.
    """
    stale = []
    for e in repo.edges_from(asset_id, RelType.DERIVED_FROM):
        parent_latest = next((v for v in repo.source_versions(e.to_asset) if v.is_latest), None)
        current = parent_latest.version_num if parent_latest is not None else None
        if rules.derivation_is_stale(e.attributes.get("derived_at_version"), current):
            stale.append(e)
    return stale


# ---------------------------------------------------------------------------
# BULK — the 100s-of-assets reality. Best-effort loops over the verbs (not one
# transaction): each item is independent, and partial progress is recoverable
# (re-running is idempotent for declare-by-spec callers that track returned ids).
# ---------------------------------------------------------------------------
def bulk_declare(repo: AssetRepo, sink: EventSink, specs: list[dict]) -> list[UUID]:
    """specs: [{asset_type, created_by, origin?}, ...] -> the minted ids, in order."""
    return [declare(repo, sink, s["asset_type"], s["created_by"], s.get("origin"))
            for s in specs]


def bulk_relate(repo: AssetRepo, sink: EventSink, edges: list[dict]) -> int:
    """edges: [{frm, to, rel_type, actor, binding_mode?, pinned_version?}, ...]."""
    for e in edges:
        relate(repo, sink, e["frm"], e["to"], e["rel_type"], e["actor"],
               binding_mode=e.get("binding_mode"), pinned_version=e.get("pinned_version"))
    return len(edges)


def bulk_relocate(repo: AssetRepo, sink: EventSink, moves: list[dict]) -> int:
    """moves: [{asset_id, new_location_uri, actor, facet?, new_revision?}, ...].

    The directory-move / reorg primitive: relocate many assets in one call.
    """
    for m in moves:
        relocate(repo, sink, m["asset_id"], m["new_location_uri"], m["actor"],
                 facet=m.get("facet", "source"), new_revision=m.get("new_revision"))
    return len(moves)
