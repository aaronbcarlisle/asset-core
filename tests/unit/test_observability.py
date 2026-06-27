"""Pure observability metrics + the stable-event-id idempotency property."""
from datetime import datetime, timedelta, timezone

from assetcore.app import observability
from assetcore.core.entities import Asset, Event
from assetcore.core.types import Lifecycle


def _asset(lifecycle, age_seconds=0, now=None):
    a = Asset(asset_type="prop", created_by="amy", lifecycle=lifecycle)
    if now is not None:
        a.created_at = now - timedelta(seconds=age_seconds)
    return a


def test_lifecycle_counts():
    assets = [_asset(Lifecycle.PROVISIONAL), _asset(Lifecycle.ACTIVE), _asset(Lifecycle.ACTIVE)]
    assert observability.lifecycle_counts(assets) == {
        "provisional": 1, "active": 2, "deprecated": 0,
    }


def test_coverage_pct_handles_empty():
    assert observability.coverage_pct(0, 0) == 100.0
    assert observability.coverage_pct(3, 4) == 75.0


def test_provisional_ages_only_provisional():
    now = datetime.now(timezone.utc)
    assets = [_asset(Lifecycle.PROVISIONAL, 100, now), _asset(Lifecycle.ACTIVE, 999, now)]
    ages = observability.provisional_ages_seconds(assets, now)
    assert len(ages) == 1 and 99 <= ages[0] <= 101


def test_event_ids_are_unique_and_stable():
    e1, e2 = Event(None, "declared"), Event(None, "declared")
    assert e1.id != e2.id          # unique per event
    assert e1.id == e1.id          # stable for one event (dedupe key)
