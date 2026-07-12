"""AssetcoreService — the composition seam (deferred here from Phase 2).

Bundles one repo + one event sink and exposes the verbs as methods, so the L2
service depends on a single object instead of threading (repo, sink) through every
route. The verbs remain free functions (pure orchestration over the ports); this
is only wiring.

Transaction boundaries: each verb's writes are atomic within the repo (e.g.
demote+insert in one tx). A true cross-verb unit of work would need the AssetRepo
port to expose transaction control, which it deliberately does not yet — when a
workflow needs several verbs to commit-or-rollback together, that becomes a new
port method, not a leak in this layer.
"""
from datetime import datetime, timezone
from typing import NamedTuple
from uuid import UUID

from assetcore.app import observability, verbs
from assetcore.core.entities import Relationship, SourceVersion
from assetcore.core.ports import AssetRepo, EventSink
from assetcore.core.types import BindingMode, RelType


class DeclareConflict(Exception):
    """A declare-with-id reused an existing id with a DIFFERENT payload.

    Distinct from an idempotent retry (same id, same asset_type/created_by), which
    is a no-op. The service maps this to HTTP 409.
    """


class DeclareResult(NamedTuple):
    """Result of declare with idempotency status."""

    id: UUID
    created: bool


class AssetcoreService:
    def __init__(self, repo: AssetRepo, sink: EventSink) -> None:
        self.repo = repo
        self.sink = sink

    def declare(self, asset_type: str, created_by: str, origin: dict | None = None,
                asset_id: UUID | None = None) -> DeclareResult:
        if asset_id is not None:
            existing = self.repo.get_asset(asset_id)
            if existing is not None:
                # idempotent re-declare, but ONLY when the payload matches: a replay
                # of the SAME declare is a no-op (created=False); a replay with a
                # different asset_type/created_by is a genuine id collision, not an
                # idempotent retry — surface it (409) rather than silently "succeed".
                if existing.asset_type != asset_type or existing.created_by != created_by:
                    raise DeclareConflict(
                        f"asset {asset_id} already exists as "
                        f"({existing.asset_type!r}, created_by={existing.created_by!r}); "
                        f"cannot re-declare as ({asset_type!r}, created_by={created_by!r})")
                return DeclareResult(id=asset_id, created=False)
        declared_id = verbs.declare(self.repo, self.sink, asset_type, created_by, origin, asset_id=asset_id)
        return DeclareResult(id=declared_id, created=True)

    def claim(self, asset_id: UUID, display_name: str, taxonomy: str, actor: str,
              reactivate: bool = False, **attrs) -> None:
        verbs.claim(self.repo, self.sink, asset_id, display_name, taxonomy, actor,
                    reactivate=reactivate, **attrs)

    def rename(self, asset_id: UUID, new_name: str, actor: str, new_taxonomy: str | None = None) -> None:
        verbs.rename(self.repo, self.sink, asset_id, new_name, actor, new_taxonomy)

    def bind_source(self, asset_id: UUID, location_uri: str, tool: str, revision: str,
                    published_by: str) -> int:
        return verbs.bind_source(self.repo, self.sink, asset_id, location_uri, tool,
                                 revision, published_by)

    def bind_runtime(self, asset_id: UUID, location_uri: str, build_id: str,
                     actor: str = "build") -> int:
        return verbs.bind_runtime(self.repo, self.sink, asset_id, location_uri, build_id, actor)

    def relate(self, frm: UUID, to: UUID, rel_type: RelType, actor: str,
               binding_mode: BindingMode | None = None, pinned_version: int | None = None) -> None:
        verbs.relate(self.repo, self.sink, frm, to, rel_type, actor, binding_mode, pinned_version)

    def set_binding(self, frm: UUID, to: UUID, binding_mode: BindingMode,
                    pinned_version: int | None = None, actor: str = "consumer") -> None:
        verbs.set_binding(self.repo, self.sink, frm, to, binding_mode, pinned_version, actor)

    def resolve(self, asset_id: UUID) -> dict:
        return verbs.resolve(self.repo, asset_id)

    def list_assets(self, created_by: str | None = None, taxonomy_prefix: str | None = None,
                    updated_since: datetime | None = None,
                    limit: int | None = None, offset: int = 0) -> list[dict]:
        """List assets with scope filters + pagination.

        `created_by` is pushed down to the repo; `taxonomy_prefix`/`updated_since`
        are applied over batch-fetched identities/facets (one query each — no N+1).
        Results are ordered by (created_at, id) for stable pagination, then sliced
        by offset/limit. `source` for the returned page is filled from the batch map.
        """
        threshold = updated_since
        if threshold is not None and threshold.tzinfo is None:
            threshold = threshold.replace(tzinfo=timezone.utc)

        assets = self.repo.list_assets(created_by=created_by)
        ids = [a.id for a in assets]
        identities = self.repo.identities(ids)
        # only need facet timestamps for updated_since; fetch once for all candidates
        sources = self.repo.latest_sources(ids)
        runtimes = self.repo.latest_runtimes(ids) if threshold is not None else {}

        filtered = []
        for asset in assets:
            identity = identities.get(asset.id)
            if taxonomy_prefix is not None:
                taxonomy = identity.taxonomy if identity is not None else None
                if taxonomy is None or not taxonomy.startswith(taxonomy_prefix):
                    continue
            if threshold is not None:
                source = sources.get(asset.id)
                runtime = runtimes.get(asset.id)
                latest_touch = max(
                    [asset.created_at]
                    + ([source.published_at] if source is not None else [])
                    + ([runtime.cooked_at] if runtime is not None else [])
                )
                if latest_touch < threshold:
                    continue
            filtered.append(asset)

        filtered.sort(key=lambda a: (a.created_at, str(a.id)))   # stable pagination order
        page = filtered[offset:] if limit is None else filtered[offset:offset + limit]

        return [{
            "id": asset.id,
            "asset_type": asset.asset_type,
            "created_by": asset.created_by,
            "created_at": asset.created_at,
            "meta": asset,
            "identity": identities.get(asset.id),
            "source": sources.get(asset.id),
        } for asset in page]

    def resolve_dependency(self, frm: UUID, to: UUID) -> SourceVersion | None:
        return verbs.resolve_dependency(self.repo, frm, to)

    def used_by(self, asset_id: UUID) -> list[Relationship]:
        return verbs.used_by(self.repo, asset_id)

    def lineage(self, asset_id: UUID) -> list[Relationship]:
        return verbs.lineage(self.repo, asset_id)

    def find_similar(self, name: str, asset_type: str | None = None, limit: int = 10) -> list[tuple]:
        return verbs.find_similar(self.repo, name, asset_type, limit)

    def backfill_worklist(self, limit: int | None = None, offset: int = 0) -> list[tuple]:
        return verbs.backfill_worklist(self.repo, limit=limit, offset=offset)

    def floating_dependencies(self, asset_id: UUID) -> list[Relationship]:
        return verbs.floating_dependencies(self.repo, asset_id)

    # --- pipeline graph + lifecycle + bulk (Phase 11) ---
    def dependents(self, asset_id: UUID, rel_types=None, max_depth: int | None = None) -> list[tuple]:
        return verbs.dependents(self.repo, asset_id, rel_types, max_depth)

    def dependencies(self, asset_id: UUID, rel_types=None, max_depth: int | None = None) -> list[tuple]:
        return verbs.dependencies(self.repo, asset_id, rel_types, max_depth)

    def relocate(self, asset_id: UUID, new_location_uri: str, actor: str,
                 facet: str = "source", new_revision: str | None = None) -> None:
        verbs.relocate(self.repo, self.sink, asset_id, new_location_uri, actor, facet, new_revision)

    def deprecate(self, asset_id: UUID, actor: str) -> None:
        verbs.deprecate(self.repo, self.sink, asset_id, actor)

    def stale_derivations(self, asset_id: UUID) -> list[Relationship]:
        return verbs.stale_derivations(self.repo, asset_id)

    def bulk_declare(self, specs: list[dict]) -> list[UUID]:
        return verbs.bulk_declare(self.repo, self.sink, specs)

    def bulk_relate(self, edges: list[dict]) -> int:
        return verbs.bulk_relate(self.repo, self.sink, edges)

    def bulk_relocate(self, moves: list[dict]) -> int:
        return verbs.bulk_relocate(self.repo, self.sink, moves)

    def metrics(self, now: datetime) -> dict:
        # Coverage via two batch queries (latest source/runtime maps), not a
        # per-asset round-trip — /metrics stays cheap as the catalog grows.
        assets = self.repo.list_assets()
        ids = [a.id for a in assets]
        total = len(assets)
        with_source = len(self.repo.latest_sources(ids))
        with_runtime = len(self.repo.latest_runtimes(ids))
        ages = observability.provisional_ages_seconds(assets, now)
        return {
            "assets_total": total,
            "lifecycle": observability.lifecycle_counts(assets),
            "source_coverage_pct": observability.coverage_pct(with_source, total),
            "runtime_coverage_pct": observability.coverage_pct(with_runtime, total),
            "provisional_count": len(ages),
            "oldest_provisional_age_seconds": round(max(ages), 1) if ages else 0.0,
        }
