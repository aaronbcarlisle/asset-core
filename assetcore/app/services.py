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
from datetime import datetime
from uuid import UUID

from assetcore.app import observability, verbs
from assetcore.core.entities import Relationship, SourceVersion
from assetcore.core.ports import AssetRepo, EventSink
from assetcore.core.types import BindingMode, RelType


class AssetcoreService:
    def __init__(self, repo: AssetRepo, sink: EventSink) -> None:
        self.repo = repo
        self.sink = sink

    def declare(self, asset_type: str, created_by: str, origin: dict | None = None) -> UUID:
        return verbs.declare(self.repo, self.sink, asset_type, created_by, origin)

    def claim(self, asset_id: UUID, display_name: str, taxonomy: str, actor: str, **attrs) -> None:
        verbs.claim(self.repo, self.sink, asset_id, display_name, taxonomy, actor, **attrs)

    def rename(self, asset_id: UUID, new_name: str, actor: str, new_taxonomy: str | None = None) -> None:
        verbs.rename(self.repo, self.sink, asset_id, new_name, actor, new_taxonomy)

    def bind_source(self, asset_id: UUID, location_uri: str, tool: str, revision: str,
                    published_by: str) -> int:
        return verbs.bind_source(self.repo, self.sink, asset_id, location_uri, tool,
                                 revision, published_by)

    def bind_runtime(self, asset_id: UUID, location_uri: str, build_id: str) -> int:
        return verbs.bind_runtime(self.repo, self.sink, asset_id, location_uri, build_id)

    def relate(self, frm: UUID, to: UUID, rel_type: RelType, actor: str,
               binding_mode: BindingMode | None = None, pinned_version: int | None = None) -> None:
        verbs.relate(self.repo, self.sink, frm, to, rel_type, actor, binding_mode, pinned_version)

    def set_binding(self, frm: UUID, to: UUID, binding_mode: BindingMode,
                    pinned_version: int | None = None) -> None:
        verbs.set_binding(self.repo, self.sink, frm, to, binding_mode, pinned_version)

    def resolve(self, asset_id: UUID) -> dict:
        return verbs.resolve(self.repo, asset_id)

    def resolve_dependency(self, frm: UUID, to: UUID) -> SourceVersion | None:
        return verbs.resolve_dependency(self.repo, frm, to)

    def used_by(self, asset_id: UUID) -> list[Relationship]:
        return verbs.used_by(self.repo, asset_id)

    def lineage(self, asset_id: UUID) -> list[Relationship]:
        return verbs.lineage(self.repo, asset_id)

    def find_similar(self, name: str, asset_type: str | None = None, limit: int = 10) -> list[tuple]:
        return verbs.find_similar(self.repo, name, asset_type, limit)

    def backfill_worklist(self) -> list[tuple]:
        return verbs.backfill_worklist(self.repo)

    def floating_dependencies(self, asset_id: UUID) -> list[Relationship]:
        return verbs.floating_dependencies(self.repo, asset_id)

    # --- pipeline graph + lifecycle + bulk (Phase 16) ---
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
        # NOTE: per-asset source/runtime lookups are O(n) round-trips on SQL
        # backends. Acceptable for now (scrape interval >> asset churn); a
        # repo-level COUNT(*)...GROUP BY coverage query is the future optimization
        # if /metrics scraping ever dominates load (PR #8 review thread).
        assets = self.repo.list_assets()
        total = len(assets)
        with_source = sum(1 for a in assets
                          if any(v.is_latest for v in self.repo.source_versions(a.id)))
        with_runtime = sum(1 for a in assets
                           if any(v.is_latest for v in self.repo.runtime_versions(a.id)))
        ages = observability.provisional_ages_seconds(assets, now)
        return {
            "assets_total": total,
            "lifecycle": observability.lifecycle_counts(assets),
            "source_coverage_pct": observability.coverage_pct(with_source, total),
            "runtime_coverage_pct": observability.coverage_pct(with_runtime, total),
            "provisional_count": len(ages),
            "oldest_provisional_age_seconds": round(max(ages), 1) if ages else 0.0,
        }
