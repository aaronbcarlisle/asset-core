"""Shared fixtures for the contract suite: a live service + SDK client factory.

`service` is a real create_app() stack behind a FastAPI TestClient (so adapters
exercise the full HTTP path L4->L3->L2->L1->L0). `make_client` builds an
AssetcoreClient with a given authority token, all sharing the one app instance so
state persists across calls within a test.

The FastAPI imports are deferred INTO the fixtures (not module-level) so importing
this conftest never needs the service extra: a bare install skips only the tests
that actually use `service`/`make_client`, while pure-SDK contract tests (e.g.
test_providers.py, which needs no service) still run. That keeps the "runs with
zero setup" promise without gating the whole package behind FastAPI.
"""
import pytest


@pytest.fixture
def service():
    pytest.importorskip("fastapi")   # skip (don't error) tests that need the service
    from fastapi.testclient import TestClient

    from assetcore.infra.broadcast_sink import BroadcastSink
    from assetcore.infra.sqlite_repo import SqliteRepo
    from assetcore.service.app import create_app

    app = create_app(repo=SqliteRepo(":memory:", check_same_thread=False), sink=BroadcastSink())
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def make_client(service):
    from assetcore.sdk.client import AssetcoreClient

    def _make(token: str = "artist-token") -> AssetcoreClient:
        return AssetcoreClient(token=token, http=service)
    return _make
