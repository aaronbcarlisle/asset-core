"""sdk/tools.py — higher-level production tools over the SDK client (L3).

Composes the universal verbs into the orchestrated, impact-previewed operations
production actually performs:
  - impact_report   : the blast radius of touching an asset (preview before acting)
  - rename_relocate : ONE safe operation = rename (identity) + relocate source +
                      relocate runtime, with the UUID stable throughout
  - relocate_prefix : a directory move — remap a path prefix across many assets

Pure orchestration over AssetcoreClient (HTTP); imports nothing below the boundary.
The CLI's `move` / `relocate-prefix` commands are thin wrappers around these, and a
GUI could be another — the logic lives here, tested once.
"""
from __future__ import annotations

from assetcore.sdk.client import AssetcoreClient
from assetcore.sdk.resolvers import ResolverRegistry, default_registry


def source_location(client: AssetcoreClient, asset_id: str) -> str | None:
    """The authored-source URI for an asset (the 'where is the source?' lookup)."""
    src = client.resolve(asset_id).get("source")
    return src["location_uri"] if src else None


def fetch_source(client: AssetcoreClient, asset_id: str,
                 registry: ResolverRegistry | None = None) -> str:
    """Open Source: resolve the asset's source URI and materialize it locally via the
    resolver registry, returning the local path to open in a DCC. The artist's
    "jump from this runtime/engine asset back to the file that authored it" — the
    URI is opaque to the core; the registry (Perforce/git/local) turns it into bytes.
    Raises if the asset has no source facet.
    """
    uri = source_location(client, asset_id)
    if uri is None:
        raise ValueError(f"asset {asset_id} has no source facet to open")
    return (registry or default_registry()).fetch(uri)


def impact_report(client: AssetcoreClient, asset_id: str, rel_types=None,
                  depth=None) -> dict:
    """The blast radius: everything that (transitively) depends on this asset,
    summarized by depth. The preview you read BEFORE a rename/move/retire."""
    deps = client.dependents(asset_id, rel_types, depth)
    by_depth: dict[int, int] = {}
    for n in deps:
        by_depth[n["depth"]] = by_depth.get(n["depth"], 0) + 1
    return {"asset_id": asset_id, "total": len(deps),
            "by_depth": dict(sorted(by_depth.items())), "dependents": deps}


def rename_relocate(client: AssetcoreClient, asset_id: str, actor: str, *,
                    new_name: str | None = None, new_taxonomy: str | None = None,
                    source_uri: str | None = None, source_rev: str | None = None,
                    runtime_uri: str | None = None) -> dict:
    """One operation across the facets an IP rename / production move touches, with
    the identity (UUID) stable. Each step is optional; relationships are never
    touched. Bytes first (relocate), then the label (rename) — independent facets,
    but this order means a reader sees the file at its new home before the new name.

    Best-effort, not one transaction (the repo port has no cross-verb tx); each verb
    is individually atomic and re-running is safe. Returns what changed.
    """
    changed = []
    if source_uri is not None:
        client.relocate(asset_id, source_uri, actor, facet="source", new_revision=source_rev)
        changed.append("source")
    elif source_rev is not None:
        # revision-only update: keep the current location, change just the revision
        cur = (client.resolve(asset_id).get("source") or {}).get("location_uri")
        if cur is not None:
            client.relocate(asset_id, cur, actor, facet="source", new_revision=source_rev)
            changed.append("source")
    if runtime_uri is not None:
        client.relocate(asset_id, runtime_uri, actor, facet="runtime")
        changed.append("runtime")
    if new_name is not None or new_taxonomy is not None:
        # rename needs a display name; a taxonomy-only change keeps the current name
        name = new_name if new_name is not None else \
            (client.resolve(asset_id).get("identity") or {}).get("display_name")
        if name is None:
            raise ValueError("a taxonomy change needs a display name (asset has none yet)")
        client.rename(asset_id, name, actor, new_taxonomy)
        changed.append("identity")
    return {"asset_id": asset_id, "changed": changed}


def plan_prefix_moves(client: AssetcoreClient, asset_ids, old_prefix: str,
                      new_prefix: str) -> dict:
    """Plan a directory move: for each asset whose source location is UNDER
    `old_prefix` (a directory boundary — `//depot/art/props` does NOT match
    `//depot/art/props2`), compute the new location by swapping the prefix. Pure
    preview — nothing is written. Returns {moves: [...], skipped: [...]}."""
    # normalize to a trailing slash so matching is at a path-segment boundary
    old_dir = old_prefix if old_prefix.endswith("/") else old_prefix + "/"
    new_dir = new_prefix if new_prefix.endswith("/") else new_prefix + "/"
    moves, skipped = [], []
    for aid in asset_ids:
        src = client.resolve(aid).get("source")
        if not src or not src["location_uri"].startswith(old_dir):
            skipped.append(aid)
            continue
        new_uri = new_dir + src["location_uri"][len(old_dir):]
        moves.append({"asset_id": aid, "from": src["location_uri"], "to": new_uri})
    return {"moves": moves, "skipped": skipped}


def relocate_prefix(client: AssetcoreClient, asset_ids, old_prefix: str,
                    new_prefix: str, actor: str) -> dict:
    """Apply a directory move planned by plan_prefix_moves (one bulk relocate)."""
    plan = plan_prefix_moves(client, asset_ids, old_prefix, new_prefix)
    if plan["moves"]:
        client.bulk_relocate([{"asset_id": m["asset_id"], "new_location_uri": m["to"],
                               "actor": actor} for m in plan["moves"]])
    return {"moved": len(plan["moves"]), "skipped": len(plan["skipped"]), "plan": plan}
