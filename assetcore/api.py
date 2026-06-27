"""
assetcore API -- the ONLY door to the asset system.

Design rule: no tool ever touches Perforce/engine paths directly to establish
identity. They call these functions, which traffic only in UUIDs. Each authority
writes only its own facet. Nothing is inferred.

This reference implementation is backend-agnostic (a DB connection with a
.execute/.fetch interface). In production this is FastAPI over asyncpg; here the
logic is what matters.
"""
from __future__ import annotations
import json, uuid
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# DECLARE  -- artists/editor summon an asset into existence. Returns a real,
#            tracked UUID immediately. Production never blocks this.
# ---------------------------------------------------------------------------
def declare(db, asset_type: str, created_by: str, origin_context: dict | None = None) -> str:
    """Mint a provisional identity. The instant an artist needs a barrel they
    get a durable ID -- Production gives it meaning later, asynchronously."""
    aid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO asset (id, lifecycle, asset_type, created_by, origin_context)"
        " VALUES (?, 'provisional', ?, ?, ?)",
        (aid, asset_type, created_by, json.dumps(origin_context or {})),
    )
    # an identity facet row exists from birth so Production can backfill in place
    db.execute("INSERT INTO facet_identity (asset_id) VALUES (?)", (aid,))
    _emit(db, aid, "declared", {"asset_type": asset_type}, created_by)
    return aid


# ---------------------------------------------------------------------------
# CLAIM  -- Production gives a provisional asset meaning (the backfill step).
# ---------------------------------------------------------------------------
def claim(db, asset_id: str, display_name: str, taxonomy: str, actor: str, **attrs):
    db.execute(
        "UPDATE facet_identity SET display_name=?, taxonomy=?, attributes=?,"
        " updated_at=now(), updated_by=? WHERE asset_id=?",
        (display_name, taxonomy, json.dumps(attrs), actor, asset_id),
    )
    db.execute("UPDATE asset SET lifecycle='active' WHERE id=?", (asset_id,))
    _emit(db, asset_id, "identity.claimed", {"name": display_name}, actor)


# ---------------------------------------------------------------------------
# RENAME  -- Production renames. Touches the identity facet ONLY. No file moves,
#            no engine changes, no batch script. The whole naming-war fix.
# ---------------------------------------------------------------------------
def rename(db, asset_id: str, new_name: str, actor: str, new_taxonomy: str | None = None):
    if new_taxonomy is None:
        db.execute("UPDATE facet_identity SET display_name=?, updated_at=now(),"
                   " updated_by=? WHERE asset_id=?", (new_name, actor, asset_id))
    else:
        db.execute("UPDATE facet_identity SET display_name=?, taxonomy=?,"
                   " updated_at=now(), updated_by=? WHERE asset_id=?",
                   (new_name, new_taxonomy, actor, asset_id))
    _emit(db, asset_id, "identity.renamed", {"name": new_name}, actor)


# ---------------------------------------------------------------------------
# BIND_SOURCE  -- artist/DCC publishes authored truth. Writes the source facet
#                 ONLY. Identity and runtime untouched.
# ---------------------------------------------------------------------------
def bind_source(db, asset_id: str, depot_path: str, dcc: str, p4_changelist: int,
                published_by: str) -> int:
    row = db.fetchone("SELECT COALESCE(MAX(version_num),0) AS v"
                      " FROM facet_source_version WHERE asset_id=?", (asset_id,))
    v = row["v"] + 1
    db.execute("UPDATE facet_source_version SET is_latest=FALSE"
               " WHERE asset_id=? AND is_latest", (asset_id,))
    db.execute(
        "INSERT INTO facet_source_version (asset_id, depot_path, p4_changelist,"
        " dcc, version_num, is_latest, published_by)"
        " VALUES (?, ?, ?, ?, ?, TRUE, ?)",
        (asset_id, depot_path, p4_changelist, dcc, v, published_by),
    )
    _emit(db, asset_id, "source.published",
          {"depot_path": depot_path, "version": v, "dcc": dcc}, published_by)
    return v


# ---------------------------------------------------------------------------
# BIND_RUNTIME  -- the build/engine reports where the cooked asset lives. Writes
#                  the runtime facet ONLY. This is what the reconciliation sync
#                  calls after reading the stamped UUID out of each .uasset.
# ---------------------------------------------------------------------------
def bind_runtime(db, asset_id: str, engine_path: str, build_id: str) -> int:
    row = db.fetchone("SELECT COALESCE(MAX(version_num),0) AS v"
                      " FROM facet_runtime_version WHERE asset_id=?", (asset_id,))
    v = row["v"] + 1
    db.execute("UPDATE facet_runtime_version SET is_latest=FALSE"
               " WHERE asset_id=? AND is_latest", (asset_id,))
    db.execute(
        "INSERT INTO facet_runtime_version (asset_id, engine_path, build_id,"
        " version_num, is_latest) VALUES (?, ?, ?, ?, TRUE)",
        (asset_id, engine_path, build_id, v),
    )
    _emit(db, asset_id, "runtime.cooked", {"engine_path": engine_path, "version": v}, "build")
    return v


# ---------------------------------------------------------------------------
# RELATE  -- record a typed edge. INSTANCE_OF / DERIVED_FROM / COMPOSED_OF /
#            DEPENDS_ON / VARIANT_OF. This is the lineage Production never had.
# ---------------------------------------------------------------------------
def relate(db, from_asset: str, to_asset: str, rel_type: str, actor: str,
           binding_mode: str | None = None, pinned_source_version: int | None = None):
    db.execute(
        "INSERT INTO relationship (from_asset, to_asset, rel_type, binding_mode,"
        " pinned_source_version) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (from_asset, to_asset, rel_type) DO UPDATE"
        " SET binding_mode=excluded.binding_mode,"
        "     pinned_source_version=excluded.pinned_source_version",
        (from_asset, to_asset, rel_type, binding_mode, pinned_source_version),
    )
    _emit(db, from_asset, "relationship.added",
          {"to": to_asset, "rel_type": rel_type, "binding_mode": binding_mode}, actor)


# ---------------------------------------------------------------------------
# RESOLVE  -- the heart of "find the other facets". Given a UUID (read off a
#             .uasset or a .ma file), return all three facets. This is the
#             single lookup that replaces the export/re-edit/re-import dig.
# ---------------------------------------------------------------------------
def resolve(db, asset_id: str) -> dict:
    ident = db.fetchone("SELECT display_name, taxonomy, status, attributes"
                        " FROM facet_identity WHERE asset_id=?", (asset_id,))
    src = db.fetchone("SELECT depot_path, version_num, p4_changelist, dcc"
                      " FROM facet_source_version WHERE asset_id=? AND is_latest", (asset_id,))
    rt = db.fetchone("SELECT engine_path, version_num, build_id"
                     " FROM facet_runtime_version WHERE asset_id=? AND is_latest", (asset_id,))
    meta = db.fetchone("SELECT asset_type, lifecycle FROM asset WHERE id=?", (asset_id,))
    return {"id": asset_id, "meta": meta, "identity": ident, "source": src, "runtime": rt}


# ---------------------------------------------------------------------------
# RESOLVE_DEPENDENCY  -- given a consumer's DEPENDS_ON edge, return the exact
#   source version it should load: latest if floating, pinned rev if pinned.
#   This is what the Maya adapter calls to decide whether to pull new materials.
# ---------------------------------------------------------------------------
def resolve_dependency(db, from_asset: str, to_asset: str) -> dict:
    edge = db.fetchone(
        "SELECT binding_mode, pinned_source_version FROM relationship"
        " WHERE from_asset=? AND to_asset=? AND rel_type='DEPENDS_ON'",
        (from_asset, to_asset))
    if edge["binding_mode"] == "pin":
        ver = edge["pinned_source_version"]
        src = db.fetchone("SELECT depot_path, version_num FROM facet_source_version"
                          " WHERE asset_id=? AND version_num=?", (to_asset, ver))
    else:  # float -> always latest authored
        src = db.fetchone("SELECT depot_path, version_num FROM facet_source_version"
                          " WHERE asset_id=? AND is_latest", (to_asset,))
    return {"mode": edge["binding_mode"], "resolved_source": src}


# ---------------------------------------------------------------------------
# LINEAGE / USAGE  -- graph traversals Production has always wanted.
# ---------------------------------------------------------------------------
def used_by(db, asset_id: str) -> list:
    """Who instances or composes this asset? (where is this barrel used)"""
    return db.fetchall(
        "SELECT from_asset, rel_type FROM relationship"
        " WHERE to_asset=? AND rel_type IN ('INSTANCE_OF','COMPOSED_OF','DERIVED_FROM')",
        (asset_id,))

def lineage(db, asset_id: str) -> list:
    """What is this derived from / instancing? (where did this come from)"""
    return db.fetchall(
        "SELECT to_asset, rel_type FROM relationship"
        " WHERE from_asset=? AND rel_type IN ('INSTANCE_OF','DERIVED_FROM','VARIANT_OF')",
        (asset_id,))


def _emit(db, asset_id, event_type, payload, actor):
    db.execute("INSERT INTO event (asset_id, event_type, payload, actor)"
               " VALUES (?, ?, ?, ?)", (asset_id, event_type, json.dumps(payload), actor))
