"""The Engine contract — ensure_identity + reconcile (ARCHITECTURE Part 4.1).

Proves the runtime-facet shape against a fake engine before any real Unreal exists.
The engine authority owns the runtime facet, so the client uses the engine token.
"""
from tests.contract.fakes import FakeEngineAdapter


def _engine(make_client) -> FakeEngineAdapter:
    return FakeEngineAdapter(make_client("engine-token"))


def test_ensure_identity_stamps_then_is_stable(make_client):
    eng = _engine(make_client)
    path = "/Game/Bob/BP_Barrel"
    eng.add_asset(path)

    aid = eng.ensure_identity(path, "prop")
    assert eng.read_stamp(path) == aid                  # stamped editor-native asset
    assert eng.ensure_identity(path, "prop") == aid     # idempotent: same identity


def test_reconcile_binds_runtime_for_stamped_assets(make_client):
    eng = _engine(make_client)
    a, b = "/Game/A", "/Game/B"
    for p in (a, b):
        eng.add_asset(p)
        eng.ensure_identity(p, "prop")

    bound = eng.reconcile("build_8821")
    assert set(bound) == {a, b} and all(v == 1 for v in bound.values())

    aid = eng.read_stamp(a)
    assert eng.client.resolve(aid)["runtime"]["location_uri"] == a


def test_reconcile_skips_unstamped_assets(make_client):
    eng = _engine(make_client)
    eng.add_asset("/Game/Stamped")
    eng.add_asset("/Game/Unstamped")           # never given an identity
    eng.ensure_identity("/Game/Stamped", "prop")

    bound = eng.reconcile("build_1")
    assert set(bound) == {"/Game/Stamped"}     # unstamped is never guessed at
