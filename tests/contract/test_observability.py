"""Stamp-coverage gate + the event-driven reconcile hook, through a live service."""
from assetcore.app.observability import stamp_coverage
from scripts.stamp_coverage_gate import run as run_gate
from tests.contract.fakes import FakeEngineAdapter


def test_stamp_coverage_gate_passes_when_all_stamped(make_client):
    eng = FakeEngineAdapter(make_client("engine-token"))
    for p in ("/Game/A", "/Game/B"):
        eng.add_asset(p)
        eng.ensure_identity(p, "prop")
    assert stamp_coverage(eng)["coverage_pct"] == 100.0
    assert run_gate(eng, threshold=100.0) == 0


def test_stamp_coverage_gate_fails_on_unstamped(make_client):
    eng = FakeEngineAdapter(make_client("engine-token"))
    eng.add_asset("/Game/Stamped")
    eng.ensure_identity("/Game/Stamped", "prop")
    eng.add_asset("/Game/Unstamped")            # never given an identity
    report = stamp_coverage(eng)
    assert report == {"stamped": 1, "total": 2, "coverage_pct": 50.0}
    assert run_gate(eng, threshold=100.0) == 1    # the build would fail


def test_event_driven_reconcile_hook_binds_immediately(make_client):
    eng = FakeEngineAdapter(make_client("engine-token"))
    eng.add_asset("/Game/JustSaved")
    version = eng.on_asset_saved("/Game/JustSaved", "live_build")
    assert version == 1
    aid = eng.read_stamp("/Game/JustSaved")
    assert eng.client.resolve(aid)["runtime"]["location_uri"] == "/Game/JustSaved"
