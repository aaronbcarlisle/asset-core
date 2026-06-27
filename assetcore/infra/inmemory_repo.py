"""Dict-backed AssetRepo + EventSink — the zero-setup backend for unit tests.

Satisfies the core.ports protocols structurally (no inheritance). The one-latest
invariant is a write-time guarantee: add_*_version demotes the prior latest as
part of the write (reusing the pure rules.demote_latest), exactly as the SQL
backends do via their one_latest_* unique indexes. This is the resolution of
PHASE1 decision #4 — the invariant lives at the storage boundary, not in the verb.
"""
from uuid import UUID

from assetcore.core.rules import demote_latest
from assetcore.core.entities import (
    Asset,
    Event,
    IdentityFacet,
    Relationship,
    RuntimeVersion,
    SourceVersion,
)
from assetcore.core.types import Lifecycle, RelType


class InMemoryRepo:
    """Satisfies core.ports.AssetRepo."""

    def __init__(self) -> None:
        self.assets: dict[UUID, Asset] = {}
        self.identities: dict[UUID, IdentityFacet] = {}
        self.sources: list[SourceVersion] = []
        self.runtimes: list[RuntimeVersion] = []
        self.rels: list[Relationship] = []

    # --- identity ---
    def create_asset(self, asset: Asset, identity: IdentityFacet) -> None:
        self.assets[asset.id] = asset
        self.identities[identity.asset_id] = identity

    def get_asset(self, asset_id: UUID) -> Asset | None:
        return self.assets.get(asset_id)

    def get_identity(self, asset_id: UUID) -> IdentityFacet | None:
        return self.identities.get(asset_id)

    def save_identity(self, identity: IdentityFacet) -> None:
        self.identities[identity.asset_id] = identity

    def set_lifecycle(self, asset_id: UUID, lifecycle: Lifecycle) -> None:
        self.assets[asset_id].lifecycle = lifecycle

    # --- source facet ---
    def add_source_version(self, v: SourceVersion) -> None:
        # enforce one_latest_source: demote the prior latest as part of the write
        demote_latest(self.source_versions(v.asset_id))
        self.sources.append(v)

    def source_versions(self, asset_id: UUID) -> list[SourceVersion]:
        # live references, by version order
        return sorted(
            (v for v in self.sources if v.asset_id == asset_id),
            key=lambda v: v.version_num,
        )

    # --- runtime facet ---
    def add_runtime_version(self, v: RuntimeVersion) -> None:
        demote_latest(self.runtime_versions(v.asset_id))
        self.runtimes.append(v)

    def runtime_versions(self, asset_id: UUID) -> list[RuntimeVersion]:
        return sorted(
            (v for v in self.runtimes if v.asset_id == asset_id),
            key=lambda v: v.version_num,
        )

    # --- relationships ---
    def add_relationship(self, r: Relationship) -> None:
        # honor the schema's UNIQUE(from, to, rel_type): relate asserts a NEW edge.
        # Flipping an existing edge is set_binding's job (upsert_relationship).
        if self.get_edge(r.from_asset, r.to_asset, r.rel_type) is not None:
            raise ValueError(
                f"edge already exists: {r.from_asset}-{r.rel_type}->{r.to_asset}"
            )
        self.rels.append(r)

    def upsert_relationship(self, r: Relationship) -> None:
        for i, existing in enumerate(self.rels):
            if (
                existing.from_asset == r.from_asset
                and existing.to_asset == r.to_asset
                and existing.rel_type == r.rel_type
            ):
                self.rels[i] = r
                return
        self.rels.append(r)

    def edges_from(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]:
        return [
            r for r in self.rels
            if r.from_asset == asset_id and (rel_type is None or r.rel_type == rel_type)
        ]

    def edges_to(self, asset_id: UUID, rel_type: RelType | None = None) -> list[Relationship]:
        return [
            r for r in self.rels
            if r.to_asset == asset_id and (rel_type is None or r.rel_type == rel_type)
        ]

    def get_edge(self, frm: UUID, to: UUID, rel_type: RelType) -> Relationship | None:
        return next(
            (
                r for r in self.rels
                if r.from_asset == frm and r.to_asset == to and r.rel_type == rel_type
            ),
            None,
        )


class InMemorySink:
    """Satisfies core.ports.EventSink. Exposes .events so tests can assert the spine fired."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)
