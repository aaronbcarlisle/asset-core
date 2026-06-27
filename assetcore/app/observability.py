"""Observability — the metrics that tell you the system is healthy (L1).

Pure functions over plain data (lifecycle mix, provisional age, coverage %) plus a
stamp-coverage gate over an engine adapter. The service exposes these at /metrics;
the gate runs in CI to fail a build that would ship unstamped assets (ARCHITECTURE
Part 8 / Phase 8). Depends on core only.
"""
from __future__ import annotations

from datetime import datetime

from assetcore.core.entities import Asset
from assetcore.core.types import Lifecycle


def lifecycle_counts(assets: list[Asset]) -> dict[str, int]:
    counts = {lc.value: 0 for lc in Lifecycle}
    for a in assets:
        counts[a.lifecycle.value] += 1
    return counts


def provisional_ages_seconds(assets: list[Asset], now: datetime) -> list[float]:
    """Age of each provisional asset — surfaces a graveyard before it forms."""
    return [(now - a.created_at).total_seconds()
            for a in assets if a.lifecycle == Lifecycle.PROVISIONAL]


def coverage_pct(bound: int, total: int) -> float:
    return 100.0 if total == 0 else round(100.0 * bound / total, 1)


def stamp_coverage(adapter) -> dict:
    """Fraction of an engine adapter's assets that carry an identity stamp.

    `adapter` is any EngineAdapter (list_assets + read_stamp). Missing stamps are
    the existential risk (stripped identity is unrecoverable), so this is what the
    CI gate watches.
    """
    paths = adapter.list_assets()
    stamped = sum(1 for p in paths if adapter.read_stamp(p) is not None)
    total = len(paths)
    return {"stamped": stamped, "total": total, "coverage_pct": coverage_pct(stamped, total)}


def stamp_coverage_gate(adapter, threshold: float = 100.0) -> tuple[bool, dict]:
    """(passes, report). passes is False when coverage is below threshold.

    Compares the RAW ratio, not the display-rounded coverage_pct: 99.95% rounds to
    100.0 but must not pass a 100% gate while unstamped assets remain.
    """
    report = stamp_coverage(adapter)
    total = report["total"]
    raw_pct = 100.0 if total == 0 else 100.0 * report["stamped"] / total
    return raw_pct >= threshold, report
