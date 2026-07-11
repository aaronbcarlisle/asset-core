from assetcore.sdk.offline import open_outbox, enqueue, pending_count, failed_count, list_pending


def test_enqueue_and_list(tmp_path):
    db = open_outbox(str(tmp_path / "outbox.db"))
    enqueue(db, "bind_source", {"asset_id": "abc", "location_uri": "//depot/foo.ma"}, asset_id="abc")
    assert pending_count(db) == 1
    assert failed_count(db) == 0
    items = list_pending(db)
    assert items[0]["verb"] == "bind_source"
    assert items[0]["status"] == "pending"
    assert items[0]["error"] is None


def test_outbox_uses_wal(tmp_path):
    db = open_outbox(str(tmp_path / "outbox.db"))
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
