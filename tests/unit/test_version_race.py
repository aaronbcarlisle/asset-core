"""Concurrent-version writes: the repo raises a typed VersionConflict on a
UNIQUE(asset_id, version_num) / one_latest race, and the verb re-reads + retries
instead of surfacing a 500."""
import pytest

from assetcore.app import verbs
from assetcore.core.entities import Asset, IdentityFacet, SourceVersion
from assetcore.core.errors import VersionConflict
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink
from assetcore.infra.sqlite_repo import SqliteRepo


def test_sqlite_add_duplicate_version_raises_version_conflict():
    repo = SqliteRepo(":memory:")
    a = Asset(asset_type="prop", created_by="amy")
    repo.create_asset(a, IdentityFacet(asset_id=a.id))
    repo.add_source_version(SourceVersion(
        asset_id=a.id, location_uri="//d/a.ma", tool="maya", revision="1", version_num=1))
    # a second writer that computed the same version_num=1 -> typed, retryable error
    with pytest.raises(VersionConflict):
        repo.add_source_version(SourceVersion(
            asset_id=a.id, location_uri="//d/a.ma", tool="maya", revision="2",
            version_num=1, is_latest=False))
    repo.close()


def test_sqlite_fk_violation_is_not_masked_as_version_conflict():
    # an insert for an unknown asset is a real bug, not a version race -> stays a
    # raw IntegrityError, never a VersionConflict.
    import sqlite3
    from uuid import uuid4
    repo = SqliteRepo(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_source_version(SourceVersion(
            asset_id=uuid4(), location_uri="//d/x.ma", tool="maya", revision="1", version_num=1))
    repo.close()


class _FlakyRepo:
    """Wraps a real repo but raises VersionConflict on the first N add_* calls."""

    def __init__(self, real, fail_times: int):
        self._real = real
        self._fails_left = fail_times
        self.add_calls = 0

    def __getattr__(self, name):
        return getattr(self._real, name)

    def add_source_version(self, v):
        self.add_calls += 1
        if self._fails_left > 0:
            self._fails_left -= 1
            raise VersionConflict("raced")
        self._real.add_source_version(v)


def test_bind_source_retries_past_transient_conflict():
    real = InMemoryRepo()
    sink = InMemorySink()
    a = verbs.declare(real, sink, "prop", "amy")
    flaky = _FlakyRepo(real, fail_times=2)      # two races, then success
    v = verbs.bind_source(flaky, sink, a, "//d/a.ma", "maya", "1", "amy")
    assert v == 1
    assert flaky.add_calls == 3                 # 2 conflicts + 1 success
    assert real.source_versions(a)[0].location_uri == "//d/a.ma"
    # exactly one source.published event (no double-emit across retries)
    assert sum(1 for e in sink.events if e.event_type == "source.published") == 1


def test_bind_source_gives_up_after_max_attempts():
    real = InMemoryRepo()
    sink = InMemorySink()
    a = verbs.declare(real, sink, "prop", "amy")
    flaky = _FlakyRepo(real, fail_times=99)     # never succeeds
    with pytest.raises(VersionConflict):
        verbs.bind_source(flaky, sink, a, "//d/a.ma", "maya", "1", "amy")
    assert sum(1 for e in sink.events if e.event_type == "source.published") == 0
