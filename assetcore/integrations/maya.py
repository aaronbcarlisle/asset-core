"""
assetcore.integrations.maya — DCC adapter stub (Maya).

This is the artist-side authority for the SOURCE facet. Two jobs:

  1. STAMP an immutable UUID into the Maya file so identity survives any rename
     or `p4 move`. In Maya this lives on a `fileInfo` entry (persisted in the
     .ma/.mb and untouched by renames). That stamp is the whole game: it's how
     `resolve()` later answers "where is the source for this thing."

  2. PUBLISH: on save/publish, call bind_source() with the depot path + CL.

Also shows the consumer side: resolve_dependency() to decide whether to pull a
newer material (float) or hold (pin) — the Scenario-3 materials flow, in Maya.

NOTE: This is a STUB. The `maya.cmds` calls are sketched and guarded so the file
imports cleanly outside Maya (for tests / Claude Code exploration). Fill in the
real cmds + your P4 client calls where marked TODO.
"""
from __future__ import annotations
import uuid as _uuid

try:
    from maya import cmds  # only present inside Maya
    IN_MAYA = True
except Exception:                       # noqa: BLE001 — fine outside Maya
    cmds = None
    IN_MAYA = False

ASSETCORE_KEY = "assetcore_uuid"        # fileInfo key that carries the identity


# --- identity stamping -------------------------------------------------------
def get_stamped_uuid() -> str | None:
    """Read the UUID stamped into the current Maya scene, if any."""
    if not IN_MAYA:
        return None
    vals = cmds.fileInfo(ASSETCORE_KEY, query=True)
    return vals[0] if vals else None


def stamp_uuid(asset_id: str) -> None:
    """Write the UUID into the scene's fileInfo. Refuses to overwrite an
    existing, different stamp — stripped identity is the one unrecoverable
    failure mode (see DESIGN.md §6.1)."""
    if not IN_MAYA:
        raise RuntimeError("stamp_uuid must run inside Maya")
    existing = get_stamped_uuid()
    if existing and existing != asset_id:
        raise RuntimeError(
            f"scene already stamped with {existing}; refusing to overwrite. "
            "Use DERIVED_FROM to fork instead.")
    cmds.fileInfo(ASSETCORE_KEY, asset_id)


# --- publish (writes the SOURCE facet) --------------------------------------
def publish(db, asset_type: str, artist: str, depot_path: str,
            p4_changelist: int, declare_if_new: bool = True) -> str:
    """Publish the current scene as authored source.

    If the scene isn't stamped yet, declare() a new provisional identity and
    stamp it (artist never waits on production). Then bind_source().
    Returns the asset_id.
    """
    from assetcore import api
    asset_id = get_stamped_uuid()
    if asset_id is None:
        if not declare_if_new:
            raise RuntimeError("scene has no assetcore UUID and declare_if_new=False")
        asset_id = api.declare(db, asset_type, created_by=f"maya:{artist}",
                               origin_context={"dcc": "maya", "depot_path": depot_path})
        stamp_uuid(asset_id)
    # TODO: ensure the file is actually submitted to P4 at this changelist first
    api.bind_source(db, asset_id, depot_path, dcc="maya",
                    p4_changelist=p4_changelist, published_by=artist)
    return asset_id


# --- consumer side: resolve a dependency (Scenario 3, in Maya) --------------
def sync_dependency(db, consumer_asset_id: str, dependency_asset_id: str) -> dict:
    """Decide which version of a dependency (e.g. a material) this scene should
    load, honoring the DEPENDS_ON edge's float/pin mode. Returns the depot path
    to fetch. The Maya adapter would then `p4 sync` + reference/import it.
    """
    from assetcore import api
    resolved = api.resolve_dependency(db, consumer_asset_id, dependency_asset_id)
    # TODO: p4 sync resolved['resolved_source']['depot_path'] and update the
    #       in-scene reference node to point at it.
    return resolved
