"""SQLite FTS5 similarity-index specifics: backfill of pre-index databases and
bounded candidate pulls (the whole point — no full-catalog scan into Python)."""
from assetcore.app import verbs
from assetcore.infra.inmemory_repo import InMemorySink
from assetcore.infra.sqlite_repo import SqliteRepo


def test_fts_backfills_a_pre_index_database(tmp_path):
    path = str(tmp_path / "assets.db")
    # build a populated db, then simulate "created before the index existed" by
    # dropping the FTS table entirely
    repo, sink = SqliteRepo(path), InMemorySink()
    a = verbs.declare(repo, sink, "prop", "amy")
    verbs.claim(repo, sink, a, "Weathered Barrel", "props/barrel", "pat")
    repo.conn.execute("DROP TABLE identity_fts")
    repo.conn.commit()
    repo.close()

    # reopening recreates AND backfills the index from facet_identity
    reopened = SqliteRepo(path)
    got = [asset.id for asset, _i in reopened.search_candidates("barrel")]
    assert got == [a]
    reopened.close()


def test_candidate_pull_is_bounded():
    repo, sink = SqliteRepo(":memory:"), InMemorySink()
    for i in range(300):
        aid = verbs.declare(repo, sink, "prop", "amy")
        verbs.claim(repo, sink, aid, f"Barrel {i}", "props/barrel", "pat")
    assert len(repo.search_candidates("barrel", limit=50)) == 50   # LIMIT honored
    # find_similar still returns its own bounded, ranked top-N
    assert len(verbs.find_similar(repo, "barrel", limit=10)) == 10
    repo.close()
