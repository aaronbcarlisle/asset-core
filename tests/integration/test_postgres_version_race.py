"""Concurrent facet-version writes against a real Postgres — gated on
ASSETCORE_TEST_DSN (runs in the CI postgres job, skips locally).

Proves the version-race fix on the production backend: two threads publishing the
same asset at once both succeed with distinct, monotonic version numbers (the
verb re-reads + retries on the UNIQUE(asset_id, version_num) conflict), and a
direct duplicate insert surfaces as a typed VersionConflict rather than a 500.
"""
import os
import threading

import pytest

DSN = os.environ.get("ASSETCORE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ASSETCORE_TEST_DSN not set")
pytest.importorskip("psycopg2")


def _repo():
    from assetcore.infra.postgres_repo import PostgresRepo
    repo = PostgresRepo(DSN)
    repo.reset()
    return repo


def test_direct_duplicate_version_raises_version_conflict():
    from assetcore.core.entities import Asset, IdentityFacet, SourceVersion
    from assetcore.core.errors import VersionConflict

    repo = _repo()
    a = Asset(asset_type="prop", created_by="amy")
    repo.create_asset(a, IdentityFacet(asset_id=a.id))
    repo.add_source_version(SourceVersion(
        asset_id=a.id, location_uri="//d/a.ma", tool="maya", revision="1", version_num=1))
    with pytest.raises(VersionConflict):
        repo.add_source_version(SourceVersion(
            asset_id=a.id, location_uri="//d/a.ma", tool="maya", revision="2",
            version_num=1, is_latest=False))
    repo.close()


def test_concurrent_bind_source_both_land_distinct_versions():
    from assetcore.app import verbs
    from assetcore.infra.inmemory_repo import InMemorySink

    # Each thread uses its OWN connection/repo (psycopg2 connections aren't shared),
    # all pointed at the same database + asset.
    setup = _repo()
    sink = InMemorySink()
    asset_id = verbs.declare(setup, sink, "prop", "amy")
    setup.close()

    from assetcore.infra.postgres_repo import PostgresRepo
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def publish(rev: str):
        repo = PostgresRepo(DSN)
        try:
            barrier.wait()                       # maximize the race window
            verbs.bind_source(repo, InMemorySink(), asset_id, f"//d/{rev}.ma", "maya", rev, "amy")
        except Exception as exc:                 # noqa: BLE001 — surface to the assert
            errors.append(exc)
        finally:
            repo.close()

    threads = [threading.Thread(target=publish, args=(r,)) for r in ("10", "20")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"a concurrent publish failed: {errors}"
    check = PostgresRepo(DSN)
    versions = sorted(v.version_num for v in check.source_versions(asset_id))
    check.close()
    assert versions == [1, 2]                     # both landed, monotonic, no dup
