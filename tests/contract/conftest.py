"""Shared fixtures for the contract suite: a live service + SDK client factory.

`service` is a real create_app() stack behind a FastAPI TestClient (so adapters
exercise the full HTTP path L4->L3->L2->L1->L0). `make_client` builds an
AssetcoreClient with a given authority token, all sharing the one app instance so
state persists across calls within a test.
"""
import pytest

# the contract suite drives adapters through a live FastAPI stack; without the
# service extra installed, skip cleanly rather than hard-error at collection (keeps
# the "runs with zero setup" promise for a bare install).
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from assetcore.infra.broadcast_sink import BroadcastSink
from assetcore.infra.sqlite_repo import SqliteRepo
from assetcore.sdk.client import AssetcoreClient
from assetcore.service.app import create_app


@pytest.fixture
def service():
    app = create_app(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def make_client(service):
    def _make(token: str = "artist-token") -> AssetcoreClient:
        return AssetcoreClient(token=token, http=service)
    return _make
