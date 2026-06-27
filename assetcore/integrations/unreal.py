"""
assetcore.integrations.unreal — engine adapter stub (Unreal).

This is the engine-side authority for the RUNTIME facet. Two jobs:

  1. STAMP the UUID into asset metadata on import/create so identity survives
     the designer renaming/moving the asset in-editor. In Unreal this lives in
     the asset's metadata tags (UMetaData / AssetRegistry tags), which persist
     with the .uasset regardless of its path. Anything created fresh in-editor
     with no source gets a NEW uuid → enters as a provisional identity →
     production backfills it (same door as a DCC-native asset).

  2. RECONCILE: a scheduled job walks the project, reads each asset's stamped
     UUID + current engine path, and calls bind_runtime(). This is what gives
     Production a live, accurate picture of where everything lives in-engine
     WITHOUT controlling it. Designers keep their freedom; Production keeps
     visibility; they touch different fields keyed to the same UUID.

The cardinal rule the engine side must honor (DESIGN.md §1, §6.1):
  * never strip the UUID
  * mint one for anything born in-editor

NOTE: STUB. The `unreal` module calls are sketched and guarded so this imports
cleanly outside the editor. Fill in real AssetRegistry / EditorAssetLibrary
calls where marked TODO.
"""
from __future__ import annotations
import uuid as _uuid

try:
    import unreal  # only present inside the Unreal Python environment
    IN_UNREAL = True
except Exception:                       # noqa: BLE001
    unreal = None
    IN_UNREAL = False

ASSETCORE_TAG = "assetcore_uuid"        # metadata tag carrying the identity


# --- identity stamping -------------------------------------------------------
def get_stamped_uuid(asset_path: str) -> str | None:
    """Read the UUID metadata tag off an asset at the given engine path."""
    if not IN_UNREAL:
        return None
    obj = unreal.EditorAssetLibrary.load_asset(asset_path)
    val = unreal.EditorAssetLibrary.get_metadata_tag(obj, ASSETCORE_TAG)
    return val or None


def stamp_uuid(asset_path: str, asset_id: str) -> None:
    """Write the UUID metadata tag. Refuses to overwrite a different existing
    stamp (stripped identity is the unrecoverable failure mode)."""
    if not IN_UNREAL:
        raise RuntimeError("stamp_uuid must run inside Unreal")
    existing = get_stamped_uuid(asset_path)
    if existing and existing != asset_id:
        raise RuntimeError(f"{asset_path} already stamped {existing}; refusing overwrite")
    obj = unreal.EditorAssetLibrary.load_asset(asset_path)
    unreal.EditorAssetLibrary.set_metadata_tag(obj, ASSETCORE_TAG, asset_id)
    unreal.EditorAssetLibrary.save_asset(asset_path)


def ensure_identity(db, asset_path: str, created_by: str = "unreal:editor") -> str:
    """Guarantee the asset at asset_path has an identity. If it's editor-native
    (no stamp), declare a provisional one and stamp it — same provisional door
    as a DCC-native asset. Returns the asset_id."""
    from assetcore import api
    existing = get_stamped_uuid(asset_path)
    if existing:
        return existing
    asset_id = api.declare(db, "engine_asset", created_by=created_by,
                           origin_context={"born_in": "editor", "engine_path": asset_path})
    stamp_uuid(asset_path, asset_id)
    return asset_id


# --- reconciliation (writes the RUNTIME facet) ------------------------------
def reconcile(db, build_id: str, asset_paths: list[str] | None = None) -> dict:
    """Walk the project (or a given list), read UUIDs, bind_runtime() for each.

    Returns a small report: matched, provisioned (editor-native, newly declared),
    and orphaned (no stamp AND couldn't mint — should be ~never; flagged loudly).
    """
    from assetcore import api
    report = {"matched": 0, "provisioned": 0, "orphaned": []}

    if asset_paths is None:
        if not IN_UNREAL:
            raise RuntimeError("reconcile needs asset_paths outside Unreal")
        # TODO: asset_paths = unreal.EditorAssetLibrary.list_assets("/Game", recursive=True)
        asset_paths = []

    for path in asset_paths:
        uid = get_stamped_uuid(path) if IN_UNREAL else None
        if uid is None:
            # editor-native asset: give it identity rather than dropping it
            try:
                uid = ensure_identity(db, path)
                report["provisioned"] += 1
            except Exception:                       # noqa: BLE001
                report["orphaned"].append(path)
                continue
        api.bind_runtime(db, uid, engine_path=path, build_id=build_id)
        report["matched"] += 1
    return report
