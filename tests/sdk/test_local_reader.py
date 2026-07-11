from fastapi.testclient import TestClient
from assetcore.sdk.replica import open_replica, upsert_asset, upsert_relation
from assetcore.sdk.local_reader import create_app


def _seed(tmp_path):
    db_path = str(tmp_path / "assetcore.db")
    conn = open_replica(db_path)
    upsert_asset(conn, {"id": "a1", "name": "hero", "asset_type": "model",
                        "created_by": "jsmith", "updated_at": "2026-07-11T00:00:00+00:00"})
    upsert_asset(conn, {"id": "a2", "name": "rig", "asset_type": "rig",
                        "created_by": "other", "updated_at": "2026-07-11T00:00:00+00:00"})
    upsert_relation(conn, "a1", "a2", "dep")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('hydrated_at', '2026-07-11T00:00:00+00:00')")
    conn.commit()
    conn.close()
    return db_path


def test_health_identity_payload(tmp_path):
    app = create_app(_seed(tmp_path), project="MyGame")
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "assetcore-local-reader"
    assert body["project"] == "MyGame"
    assert body["hydrated_at"] == "2026-07-11T00:00:00+00:00"


def test_resolve_returns_asset_and_dependencies(tmp_path):
    app = create_app(_seed(tmp_path), project="MyGame")
    r = TestClient(app).get("/resolve/a1")
    assert r.status_code == 200
    assert r.json()["asset"]["name"] == "hero"
    assert r.json()["dependencies"] == [{"to_id": "a2", "rel_type": "dep", "binding_mode": "float"}]


def test_resolve_missing_is_404(tmp_path):
    app = create_app(_seed(tmp_path), project="MyGame")
    assert TestClient(app).get("/resolve/nope").status_code == 404


def test_dependents_reverse_lookup(tmp_path):
    app = create_app(_seed(tmp_path), project="MyGame")
    r = TestClient(app).get("/dependents/a2")
    assert [d["from_id"] for d in r.json()] == ["a1"]


def test_assets_filter_by_created_by(tmp_path):
    app = create_app(_seed(tmp_path), project="MyGame")
    r = TestClient(app).get("/assets", params={"created_by": "jsmith"})
    assert [a["id"] for a in r.json()] == ["a1"]
