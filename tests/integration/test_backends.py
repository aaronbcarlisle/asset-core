"""The shared scenario suite, run against every real storage backend.

Proves the AssetRepo port: the identical assertions from scenarios_common pass on
sqlite (always) and postgres (when ASSETCORE_TEST_DSN is set + psycopg2 present).
Postgres is skipped — not silently passed — when unavailable, so a green run here
always means at least sqlite genuinely round-tripped every scenario.

EventSink stays InMemorySink across backends: it's a separate port (Phase 3's
notify sink persists events); Phase 2 is about proving the *storage* port.
"""
import os

import pytest

from assetcore.infra.inmemory_repo import InMemorySink
from assetcore.infra.sqlite_repo import SqliteRepo
from tests.scenarios_common import ALL_SCENARIOS


def _sqlite_backend():
    return SqliteRepo(":memory:"), InMemorySink()


def _build_backends():
    backends = [pytest.param(_sqlite_backend, id="sqlite")]
    dsn = os.environ.get("ASSETCORE_TEST_DSN")
    if dsn:
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            # DSN set but driver missing: report an explicit skip, don't silently omit
            backends.append(pytest.param(
                None, id="postgres",
                marks=pytest.mark.skip(reason="ASSETCORE_TEST_DSN set but psycopg2 not installed")))
        else:
            from assetcore.infra.postgres_repo import PostgresRepo

            def _postgres_backend(_dsn=dsn):
                repo = PostgresRepo(_dsn)
                repo.reset()                       # clean slate per test
                return repo, InMemorySink()

            backends.append(pytest.param(_postgres_backend, id="postgres"))
    return backends


BACKENDS = _build_backends()


@pytest.mark.parametrize("make_backend", BACKENDS)
@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_scenario_on_backend(scenario, make_backend):
    repo, sink = make_backend()
    try:
        scenario(repo, sink)
    finally:
        close = getattr(repo, "close", None)
        if close is not None:
            close()


@pytest.mark.parametrize("make_backend", BACKENDS)
def test_search_candidates_narrowing_contract(make_backend):
    from assetcore.app import verbs
    repo, sink = make_backend()
    try:
        b1 = verbs.declare(repo, sink, "prop", "amy")
        verbs.claim(repo, sink, b1, "Weathered Barrel", "props/containers/barrel", "pat")
        b2 = verbs.declare(repo, sink, "prop", "ben")
        verbs.claim(repo, sink, b2, "Mossy Barrel", "props/containers/barrel", "pat")
        crate = verbs.declare(repo, sink, "prop", "amy")
        verbs.claim(repo, sink, crate, "Wooden Crate", "props/containers/crate", "pat")
        tagged = verbs.declare(repo, sink, "material", "mo")
        verbs.claim(repo, sink, tagged, "Oak Planks", "mat/wood", "pat")
        ident = repo.get_identity(tagged)
        ident.tags = ["barrel", "stave"]
        repo.save_identity(ident)                      # tag-only match
        unclaimed = verbs.declare(repo, sink, "prop", "amy")   # blank identity

        got = {a.id for a, _i in repo.search_candidates("barrel")}
        assert {b1, b2, tagged} <= got                 # name + tag matches surface
        assert crate not in got and unclaimed not in got

        typed = {a.id for a, _i in repo.search_candidates("barrel", asset_type="prop")}
        assert typed == {b1, b2}                       # type filter applies

        assert repo.search_candidates("") == []        # no tokens -> no candidates
        # a rename + re-file is reflected (the index follows save_identity)
        verbs.rename(repo, sink, b1, "Weathered Cask", "pat",
                     new_taxonomy="props/containers/cask")
        got = {a.id for a, _i in repo.search_candidates("barrel", asset_type="prop")}
        assert got == {b2}
        # find_similar output is consistent through the narrowed path
        hits = verbs.find_similar(repo, "barrel", asset_type="prop")
        assert [a.id for a, _i, _s in hits] == [b2]
    finally:
        close = getattr(repo, "close", None)
        if close is not None:
            close()


@pytest.mark.parametrize("make_backend", BACKENDS)
def test_batch_getters_and_created_by_filter(make_backend):
    from assetcore.app import verbs
    repo, sink = make_backend()
    try:
        a = verbs.declare(repo, sink, "prop", "amy")
        b = verbs.declare(repo, sink, "prop", "ben")
        verbs.claim(repo, sink, a, "Barrel", "props/barrel", "pat")
        verbs.bind_source(repo, sink, a, "//d/a.ma", "maya", "1", "amy")
        verbs.bind_runtime(repo, sink, a, "/Game/a", "build-1")

        # created_by pushed down to the repo
        assert {x.id for x in repo.list_assets(created_by="amy")} == {a}

        ids = [a, b]
        idents = repo.identities(ids)
        assert idents[a].display_name == "Barrel"
        assert idents[b].display_name is None            # provisional, unclaimed

        sources = repo.latest_sources(ids)
        assert set(sources) == {a} and sources[a].location_uri == "//d/a.ma"
        runtimes = repo.latest_runtimes(ids)
        assert set(runtimes) == {a} and runtimes[a].location_uri == "/Game/a"

        # empty input -> empty maps (no malformed IN () query)
        assert repo.identities([]) == {}
        assert repo.latest_sources([]) == {}
        assert repo.latest_runtimes([]) == {}
    finally:
        close = getattr(repo, "close", None)
        if close is not None:
            close()
