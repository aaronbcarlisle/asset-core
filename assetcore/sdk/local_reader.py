from __future__ import annotations
import json
import sqlite3
from fastapi import FastAPI, HTTPException

from assetcore.sdk.hub import PipelineConfig
from assetcore.sdk.replica import open_replica


def _resolve_shape(record: dict) -> dict:
    """Project a stored record to the central ResolveResponse shape."""
    return {"id": record["id"], "meta": record.get("meta"),
            "identity": record.get("identity"), "source": record.get("source"),
            "runtime": record.get("runtime")}


def _summary_shape(record: dict) -> dict:
    """Project a stored record to the central AssetSummaryOut shape."""
    meta = record.get("meta") or {}
    return {"id": record["id"],
            "asset_type": record.get("asset_type") or meta.get("asset_type"),
            "created_by": record.get("created_by") or meta.get("created_by"),
            "created_at": record.get("created_at"),
            "meta": record.get("meta"), "identity": record.get("identity"),
            "source": record.get("source")}


def create_app(replica_path: str, project: str) -> FastAPI:
    app = FastAPI(title="assetcore-local-reader")

    def db() -> sqlite3.Connection:
        # per-request connection: hydrate atomically replaces the file underneath us
        return open_replica(replica_path)

    @app.get("/health")
    def health():
        conn = db()
        row = conn.execute("SELECT value FROM meta WHERE key='hydrated_at'").fetchone()
        conn.close()
        return {"app": "assetcore-local-reader", "project": project,
                "hydrated_at": row["value"] if row else None}

    @app.get("/resolve/{asset_id}")
    def resolve(asset_id: str):
        # Returns the central ResolveResponse shape (id/meta/identity/source/runtime)
        # PLUS `dependencies` — an additive, local-only key (central resolve has no
        # dependency list). The core facet fields match central exactly.
        conn = db()
        row = conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            conn.close()
            raise HTTPException(status_code=404, detail="unknown asset")
        deps = conn.execute(
            "SELECT to_id, rel_type, binding_mode FROM relations WHERE from_id=? ORDER BY to_id",
            (asset_id,)).fetchall()
        conn.close()
        out = _resolve_shape(json.loads(row["payload_json"]))
        out["dependencies"] = [{"asset_id": d["to_id"], "rel_type": d["rel_type"],
                                "binding_mode": d["binding_mode"]} for d in deps]
        return out

    @app.get("/dependents/{asset_id}")
    def dependents(asset_id: str):
        # Direct (depth-1) reverse edges, keyed like the central GraphNodeOut
        # (asset_id/depth/rel_type) so callers see one shape. The replica holds
        # direct edges only, so this is not the transitive closure central returns.
        conn = db()
        rows = conn.execute(
            "SELECT from_id, rel_type, binding_mode FROM relations WHERE to_id=? ORDER BY from_id",
            (asset_id,)).fetchall()
        conn.close()
        return [{"asset_id": r["from_id"], "depth": 1, "rel_type": r["rel_type"],
                 "binding_mode": r["binding_mode"]} for r in rows]

    @app.get("/assets")
    def assets(created_by: str | None = None, taxonomy_prefix: str | None = None,
               updated_since: str | None = None):
        # Returns AssetSummaryOut-shaped records (matching central /assets), with
        # created_by / taxonomy_prefix / updated_since filters that actually work
        # (taxonomy and updated_at are extracted columns, not nested-only fields).
        conn = db()
        sql, params = "SELECT payload_json FROM assets WHERE 1=1", []
        if created_by:
            sql += " AND created_by=?"
            params.append(created_by)
        if taxonomy_prefix:
            sql += r" AND taxonomy IS NOT NULL AND taxonomy LIKE ? ESCAPE '\'"
            params.append(taxonomy_prefix.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_") + "%")
        if updated_since:
            sql += " AND updated_at IS NOT NULL AND updated_at >= ?"
            params.append(updated_since)
        rows = conn.execute(sql + " ORDER BY id", params).fetchall()
        conn.close()
        return [_summary_shape(json.loads(r["payload_json"])) for r in rows]

    return app


def serve_local(pipeline: PipelineConfig) -> None:
    import uvicorn
    app = create_app(pipeline.local_cache, pipeline.scope.get("assetcore_project", ""))
    uvicorn.run(app, host="127.0.0.1", port=pipeline.local_reader_port, log_level="warning")
