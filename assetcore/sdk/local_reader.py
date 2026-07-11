from __future__ import annotations
import json
import sqlite3
from fastapi import FastAPI, HTTPException

from assetcore.sdk.hub import PipelineConfig
from assetcore.sdk.replica import open_replica


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
        conn = db()
        row = conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            conn.close()
            raise HTTPException(status_code=404, detail="unknown asset")
        deps = conn.execute(
            "SELECT to_id, rel_type, binding_mode FROM relations WHERE from_id=? ORDER BY to_id",
            (asset_id,)).fetchall()
        conn.close()
        return {"asset": json.loads(row["payload_json"]),
                "dependencies": [dict(d) for d in deps]}

    @app.get("/dependents/{asset_id}")
    def dependents(asset_id: str):
        conn = db()
        rows = conn.execute(
            "SELECT from_id, rel_type, binding_mode FROM relations WHERE to_id=? ORDER BY from_id",
            (asset_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @app.get("/assets")
    def assets(created_by: str | None = None, taxonomy_prefix: str | None = None,
               updated_since: str | None = None):
        conn = db()
        sql, params = "SELECT payload_json FROM assets WHERE 1=1", []
        if created_by:
            sql += " AND created_by=?"
            params.append(created_by)
        if updated_since:
            sql += " AND updated_at >= ?"
            params.append(updated_since)
        rows = conn.execute(sql + " ORDER BY id", params).fetchall()
        conn.close()
        out = [json.loads(r["payload_json"]) for r in rows]
        if taxonomy_prefix:
            out = [a for a in out if (a.get("taxonomy") or "").startswith(taxonomy_prefix)]
        return out

    return app


def serve_local(pipeline: PipelineConfig) -> None:
    import uvicorn
    app = create_app(pipeline.local_cache, pipeline.scope.get("assetcore_project", ""))
    uvicorn.run(app, host="127.0.0.1", port=pipeline.local_reader_port, log_level="warning")
