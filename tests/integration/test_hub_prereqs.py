"""Integration coverage for the hub prerequisites in spec section 7.5."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.sdk.client import AssetcoreClient
from assetcore.service.app import create_app

ARTIST = {"X-Assetcore-Token": "artist-token"}
PROD = {"X-Assetcore-Token": "prod-token"}


@pytest.fixture
def client():
    app = create_app(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
    with TestClient(app) as c:
        yield c


def test_declare_with_client_id_is_idempotent_and_sdk_accepts_id(client):
    sdk = AssetcoreClient(token="artist-token", http=client)
    aid = str(uuid4())

    first = client.post(
        "/assets",
        json={"id": aid, "asset_type": "prop", "created_by": "artist-a"},
        headers=ARTIST,
    )
    second = client.post(
        "/assets",
        json={"id": aid, "asset_type": "prop", "created_by": "artist-a"},
        headers=ARTIST,
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == aid == second.json()["id"]
    assert sdk.declare("prop", "artist-a", asset_id=aid) == aid


def test_list_assets_supports_scope_filters_and_summary_shape(client):
    sdk = AssetcoreClient(token="artist-token", http=client)

    old_aid = sdk.declare("prop", "artist-a")
    client.post(
        f"/assets/{old_aid}/claim",
        json={"display_name": "Old Barrel", "taxonomy": "props/old/barrel", "actor": "prod"},
        headers=PROD,
    )

    cutoff = datetime.now(timezone.utc).isoformat()

    props_aid = sdk.declare("prop", "artist-a")
    env_aid = sdk.declare("set", "artist-b")
    client.post(
        f"/assets/{props_aid}/claim",
        json={"display_name": "New Barrel", "taxonomy": "props/new/barrel", "actor": "prod"},
        headers=PROD,
    )
    client.post(
        f"/assets/{env_aid}/claim",
        json={"display_name": "Forest", "taxonomy": "env/forest", "actor": "prod"},
        headers=PROD,
    )

    by_creator = sdk.list_assets(created_by="artist-a")
    by_taxonomy = sdk.list_assets(taxonomy_prefix="props/")
    recent = sdk.list_assets(updated_since=cutoff)

    by_creator_ids = {a["id"] for a in by_creator}
    by_taxonomy_ids = {a["id"] for a in by_taxonomy}
    recent_ids = {a["id"] for a in recent}

    assert {old_aid, props_aid}.issubset(by_creator_ids)
    assert {old_aid, props_aid}.issubset(by_taxonomy_ids)
    assert env_aid not in by_taxonomy_ids
    assert old_aid not in recent_ids
    assert {props_aid, env_aid}.issubset(recent_ids)

    sample = next(a for a in by_creator if a["id"] == props_aid)
    assert {"id", "asset_type", "created_by", "created_at", "meta", "identity", "source"} <= set(sample)
    assert sample["identity"]["taxonomy"] == "props/new/barrel"


def test_get_source_returns_none_until_source_is_bound(client):
    sdk = AssetcoreClient(token="artist-token", http=client)
    aid = sdk.declare("prop", "artist-a")

    assert sdk.get_source(aid) is None

    sdk.bind_source(aid, "//depot/props/barrel.ma", "maya", "101", "artist-a")
    source = sdk.get_source(aid)
    assert source is not None
    assert source["location_uri"] == "//depot/props/barrel.ma"


def test_relate_rejects_duplicate_edges_with_clear_message(client):
    sdk = AssetcoreClient(token="artist-token", http=client)
    frm = sdk.declare("set", "artist-a")
    to = sdk.declare("prop", "artist-a")
    body = {"from_asset": frm, "to_asset": to, "rel_type": "COMPOSED_OF", "actor": "artist-a"}

    first = client.post("/relate", json=body, headers=ARTIST)
    second = client.post("/relate", json=body, headers=ARTIST)

    assert first.status_code == 204, first.text
    assert second.status_code == 400, second.text
    assert "duplicate edge" in second.json()["detail"]
