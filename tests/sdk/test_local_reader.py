"""Local reader tests, seeded through the REAL hydrate pipeline.

The earlier version of these tests hand-built replica rows with top-level
`taxonomy`/`updated_at` fields that real `hydrate_cache` never produces, so the
filters looked tested while being broken end-to-end. These seed a real
TestClient-backed central service, hydrate from it, and then exercise the reader —
so the replica shape under test is exactly what production writes.
"""
import pytest

pytest.importorskip("fastapi")

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.sdk.client import AssetcoreClient
from assetcore.sdk.hub import PipelineConfig
from assetcore.sdk.local_reader import create_app as create_reader
from assetcore.sdk.replica import hydrate_cache
from assetcore.service.app import create_app as create_service


def _central():
    app = create_service(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
    return AssetcoreClient(token="artist-token", http=TestClient(app))


def _prod(central):
    return AssetcoreClient(token="prod-token", http=central._http)


def _pipeline(tmp_path, project="GAME"):
    return PipelineConfig(
        central_url="http://central", local_cache=str(tmp_path / "assetcore.db"),
        outbox_path=str(tmp_path / "outbox.db"), config_path=str(tmp_path / "p.toml"),
        scope={"assetcore_project": project}, recent_days=3650,
    )


def _ctx(user="jsmith"):
    return {"user_name": user, "workspace_root": "", "changelist": "1", "uproject_path": "",
            "depot_path": "", "synced_project_folder": "", "pipeline_config_path": "p.toml"}


def _seed_and_hydrate(tmp_path):
    """Build a small central graph, hydrate a replica from it, return (reader, ids)."""
    central = _central()
    prod = _prod(central)
    hero = central.declare("model", "jsmith")
    rig = central.declare("rig", "jsmith")
    central.bind_source(hero, "//depot/hero.ma", "maya", "12", "jsmith")
    prod.claim(hero, "Hero", "GAME/chars/hero", "pat")
    prod.claim(rig, "Hero Rig", "GAME/chars/hero_rig", "pat")
    central.relate(hero, rig, "DEPENDS_ON", binding_mode="float")

    pipeline = _pipeline(tmp_path)
    hydrate_cache(pipeline, _ctx(), central, now_iso=datetime.now(timezone.utc).isoformat())
    reader = TestClient(create_reader(pipeline.local_cache, "GAME"))
    return reader, central, {"hero": hero, "rig": rig}


def test_health_identity_payload(tmp_path):
    reader, _, _ = _seed_and_hydrate(tmp_path)
    body = reader.get("/health").json()
    assert body["app"] == "assetcore-local-reader"
    assert body["project"] == "GAME"
    assert body["hydrated_at"] is not None


def test_resolve_matches_central_shape(tmp_path):
    reader, central, ids = _seed_and_hydrate(tmp_path)
    local = reader.get(f"/resolve/{ids['hero']}").json()
    remote = central.resolve(ids["hero"])

    # the core facet fields are shape-identical to central resolve()
    assert set(remote) <= set(local)                       # local adds `dependencies`
    for key in ("id", "meta", "identity", "source", "runtime"):
        assert key in local
    assert local["identity"]["display_name"] == "Hero"
    assert local["identity"]["taxonomy"] == "GAME/chars/hero"
    assert local["source"]["location_uri"] == "//depot/hero.ma"
    # dependencies is the additive local-only key, keyed like GraphNodeOut
    assert local["dependencies"] == [
        {"asset_id": ids["rig"], "rel_type": "DEPENDS_ON", "binding_mode": "float"}]


def test_resolve_missing_is_404(tmp_path):
    reader, _, _ = _seed_and_hydrate(tmp_path)
    assert reader.get("/resolve/00000000-0000-0000-0000-000000000000").status_code == 404


def test_dependents_reverse_lookup_graphnode_shape(tmp_path):
    reader, _, ids = _seed_and_hydrate(tmp_path)
    deps = reader.get(f"/dependents/{ids['rig']}").json()
    assert deps == [{"asset_id": ids["hero"], "depth": 1, "rel_type": "DEPENDS_ON",
                     "binding_mode": "float"}]


def test_assets_summary_shape_matches_central(tmp_path):
    reader, central, ids = _seed_and_hydrate(tmp_path)
    local = {a["id"]: a for a in reader.get("/assets").json()}
    remote = {a["id"]: a for a in central.list_assets()}
    assert set(local) == set(remote)
    sample = local[ids["hero"]]
    # AssetSummaryOut shape (matches central /assets)
    assert {"id", "asset_type", "created_by", "created_at", "meta", "identity", "source"} <= set(sample)
    assert sample["identity"]["taxonomy"] == "GAME/chars/hero"


def test_assets_taxonomy_prefix_filter_works(tmp_path):
    # the bug this suite exists for: taxonomy is nested under identity, so the old
    # top-level filter returned nothing. It must now actually filter.
    reader, _, ids = _seed_and_hydrate(tmp_path)
    hits = {a["id"] for a in reader.get("/assets", params={"taxonomy_prefix": "GAME/chars/hero"}).json()}
    assert hits == {ids["hero"], ids["rig"]}
    narrow = {a["id"] for a in reader.get("/assets", params={"taxonomy_prefix": "GAME/chars/hero_rig"}).json()}
    assert narrow == {ids["rig"]}
    assert reader.get("/assets", params={"taxonomy_prefix": "OTHER/"}).json() == []


def test_assets_updated_since_filter_works(tmp_path):
    reader, _, _ = _seed_and_hydrate(tmp_path)
    # everything was just touched -> a far-past cutoff returns all, a far-future none
    assert len(reader.get("/assets", params={"updated_since": "2000-01-01T00:00:00+00:00"}).json()) == 2
    assert reader.get("/assets", params={"updated_since": "2999-01-01T00:00:00+00:00"}).json() == []


def test_assets_created_by_filter_works(tmp_path):
    reader, central, _ = _seed_and_hydrate(tmp_path)
    assert len(reader.get("/assets", params={"created_by": "jsmith"}).json()) == 2
    assert reader.get("/assets", params={"created_by": "nobody"}).json() == []
