import json

from assetcore.sdk.hub import PipelineConfig
from assetcore.sdk.replica import open_replica, hydrate_cache, get_asset, upsert_asset

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
    def list_assets(self, created_by=None, taxonomy_prefix=None, updated_since=None,
                    limit=None, offset=0):
        rows = ([a for a in self.assets.values() if a.get("created_by") == created_by]
                if created_by else list(self.assets.values()))
        return rows[offset:offset + limit] if limit is not None else rows[offset:]
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
    # records are stored in the central resolve() shape (name lives under identity)
    assert get_asset(db, "a1")["identity"]["display_name"] == "hero"
    assert get_asset(db, "a2")["identity"]["display_name"] == "hero_rig"  # via closure
    # the extracted `name` column tracks identity.display_name for cheap listing
    assert db.execute("SELECT name FROM assets WHERE id='a1'").fetchone()[0] == "hero"
    meta = db.execute("SELECT value FROM meta WHERE key='hydrated_at'").fetchone()
    assert meta[0] == NOW

def test_open_replica_migrates_legacy_cache(tmp_path):
    # simulate a cache from an older version: no `taxonomy` column, asset_type NOT NULL
    import sqlite3
    path = str(tmp_path / "assetcore.db")
    legacy = sqlite3.connect(path)
    legacy.executescript(
        "CREATE TABLE assets ("
        " id TEXT PRIMARY KEY, name TEXT NOT NULL, asset_type TEXT NOT NULL, status TEXT,"
        " created_by TEXT, updated_at TEXT, payload_json TEXT NOT NULL);"
        "CREATE TABLE relations (from_id TEXT, to_id TEXT, rel_type TEXT,"
        " binding_mode TEXT NOT NULL DEFAULT 'float', PRIMARY KEY (from_id, to_id, rel_type));"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
    legacy.execute("INSERT INTO assets (id, name, asset_type, payload_json) VALUES (?,?,?,?)",
                   ("old1", "Legacy", "model", json.dumps({"id": "old1", "name": "Legacy"})))
    legacy.commit()
    legacy.close()

    # open_replica upgrades in place: taxonomy exists, asset_type is now nullable,
    # and the existing row is preserved
    conn = open_replica(path)
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(assets)").fetchall()}
    assert "taxonomy" in cols
    assert cols["asset_type"]["notnull"] == 0
    assert get_asset(conn, "old1")["name"] == "Legacy"

    # and an offline/partial upsert with asset_type=None no longer violates NOT NULL
    upsert_asset(conn, {"id": "new1", "identity": {"display_name": "New"}})
    assert conn.execute("SELECT asset_type FROM assets WHERE id='new1'").fetchone()[0] is None
    conn.close()


def test_hydrate_pages_beyond_one_list_page(tmp_path):
    # a catalog larger than one /assets page must be fully hydrated, not truncated
    class PagingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.assets = {f"a{i}": {"id": f"a{i}", "name": f"n{i}", "asset_type": "model",
                                     "created_by": "jsmith", "updated_at": NOW}
                           for i in range(1100)}
            self.deps = {}

    pipeline = _pipeline(tmp_path)
    result = hydrate_cache(pipeline, _ctx(tmp_path), PagingClient(), now_iso=NOW)
    assert result["assets"] == 1100        # all pages pulled, nothing dropped at 500


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
