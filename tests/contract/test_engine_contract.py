"""The Engine contract — ensure_identity + reconcile (ARCHITECTURE Part 4.1).

Parameterized over the fake engine AND the real UnrealAdapter (run via a faithful
fake of unreal.EditorAssetLibrary) — the same Part-5 thesis as the DCC side. Each
builder returns (adapter, create_asset) so the suite can populate either engine's
asset listing uniformly. The engine authority owns the runtime facet.
"""
import pytest

from assetcore.integrations.unreal import UnrealAdapter
from tests.contract.fakes import FakeEngineAdapter, FakeUnrealEditor


def _fake_engine(make_client):
    adapter = FakeEngineAdapter(make_client("engine-token"))
    return adapter, adapter.add_asset


def _unreal(make_client):
    editor = FakeUnrealEditor()
    adapter = UnrealAdapter(make_client("engine-token"), editor=editor)
    return adapter, editor.create_asset


ENGINE_ADAPTERS = [
    pytest.param(_fake_engine, id="fake"),
    pytest.param(_unreal, id="unreal"),
]


@pytest.fixture(params=ENGINE_ADAPTERS)
def engine(request, make_client):
    """Returns (adapter, create_asset(path))."""
    return request.param(make_client)


def test_ensure_identity_stamps_then_is_stable(engine):
    adapter, create_asset = engine
    path = "/Game/Bob/BP_Barrel"
    create_asset(path)

    aid = adapter.ensure_identity(path, "prop")
    assert adapter.read_stamp(path) == aid                  # stamped editor-native asset
    assert adapter.ensure_identity(path, "prop") == aid     # idempotent: same identity


def test_reconcile_binds_runtime_for_stamped_assets(engine):
    adapter, create_asset = engine
    a, b = "/Game/A", "/Game/B"
    for p in (a, b):
        create_asset(p)
        adapter.ensure_identity(p, "prop")

    bound = adapter.reconcile("build_8821")
    assert set(bound) == {a, b} and all(v == 1 for v in bound.values())

    aid = adapter.read_stamp(a)
    assert adapter.client.resolve(aid)["runtime"]["location_uri"] == a


def test_reconcile_skips_unstamped_assets(engine):
    adapter, create_asset = engine
    create_asset("/Game/Stamped")
    create_asset("/Game/Unstamped")            # never given an identity
    adapter.ensure_identity("/Game/Stamped", "prop")

    bound = adapter.reconcile("build_1")
    assert set(bound) == {"/Game/Stamped"}     # unstamped is never guessed at


def test_on_asset_saved_binds_immediately(engine):
    adapter, create_asset = engine
    create_asset("/Game/JustSaved")
    version = adapter.on_asset_saved("/Game/JustSaved", "live_build")  # save-hook path
    assert version == 1
    aid = adapter.read_stamp("/Game/JustSaved")
    assert adapter.client.resolve(aid)["runtime"]["location_uri"] == "/Game/JustSaved"
