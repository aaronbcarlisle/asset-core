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
    rel_type = RelType(rel_type)
    if binding_mode is not None:
        binding_mode = BindingMode(binding_mode)
    r = Relationship(from_asset=frm, to_asset=to, rel_type=rel_type,
                     binding_mode=binding_mode, pinned_version=pinned_version)
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
