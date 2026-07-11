from assetcore.sdk.hub import PipelineConfig
from assetcore.sdk.replica import open_replica, hydrate_cache, get_asset

NOW = "2026-07-11T12:00:00+00:00"

class FakeClient:
    """list_assets returns authored assets; resolve + dependencies drive closure."""
    def __init__(self):
        self.assets = {
            "a1": {"id": "a1", "name": "hero", "asset_type": "model", "status": "wip",
                   "created_by": "jsmith", "updated_at": NOW},
            "a2": {"id": "a2", "name": "hero_rig", "asset_type": "rig", "status": "wip",
                   "created_by": "other", "updated_at": NOW},
        }
        self.deps = {"a1": [{"asset_id": "a2", "depth": 1, "rel_type": "DEPENDS_ON"}], "a2": []}
    def list_assets(self, created_by=None, taxonomy_prefix=None, updated_since=None):
        if created_by:
            return [a for a in self.assets.values() if a.get("created_by") == created_by]
        return list(self.assets.values())
    def resolve(self, asset_id):
        if asset_id not in self.assets:
            return None
        a = self.assets[asset_id]
        return {"id": asset_id, "meta": {"asset_type": a["asset_type"]},
                "identity": {"display_name": a["name"]}, "source": None, "runtime": None}
    def dependencies(self, asset_id, rel_types=None, depth=None):
        return self.deps.get(asset_id, [])

def _pipeline(tmp_path):
    return PipelineConfig(
        central_url="http://central:8080", local_cache=str(tmp_path / "assetcore.db"),
        outbox_path=str(tmp_path / "o.db"), config_path=str(tmp_path / "pipeline.toml"),
        scope={"assetcore_project": "MyGame"}, recent_days=14,
    )

def _ctx(tmp_path):
    return {"workspace_root": str(tmp_path), "changelist": "1", "uproject_path": "",
            "depot_path": "//d/", "user_name": "jsmith", "synced_project_folder": "G",
            "pipeline_config_path": str(tmp_path / "pipeline.toml")}

def test_hydrate_pulls_user_assets_and_dependency_closure(tmp_path):
    pipeline = _pipeline(tmp_path)
    result = hydrate_cache(pipeline, _ctx(tmp_path), FakeClient(), now_iso=NOW)
    assert result["assets"] == 2      # a1 (authored) + a2 (dependency closure)
    assert result["relations"] == 1
    db = open_replica(pipeline.local_cache)
    assert get_asset(db, "a1")["name"] == "hero"
    assert get_asset(db, "a2")["name"] == "hero_rig"   # pulled via closure, not assignment
    meta = db.execute("SELECT value FROM meta WHERE key='hydrated_at'").fetchone()
    assert meta[0] == NOW

def test_hydrate_swaps_atomically_and_leaves_no_temp(tmp_path):
    pipeline = _pipeline(tmp_path)
    hydrate_cache(pipeline, _ctx(tmp_path), FakeClient(), now_iso=NOW)
    conn = open_replica(pipeline.local_cache)
    assert get_asset(conn, "a1") is not None
    conn.close()   # Windows: os.replace fails while a handle is open — close first
    hydrate_cache(pipeline, _ctx(tmp_path), FakeClient(), now_iso=NOW)
    import glob, os
    assert glob.glob(os.path.join(os.path.dirname(pipeline.local_cache), "*.tmp")) == []
    conn = open_replica(pipeline.local_cache)
    assert get_asset(conn, "a1") is not None   # swapped replica is intact
