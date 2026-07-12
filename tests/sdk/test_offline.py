from assetcore.sdk.offline import (
    open_outbox, enqueue, pending_count, failed_count, list_pending,
    replay_outbox, retry_failed, list_failed, mark_failed,
)


class FakeClient:
    def __init__(self, fail_verbs=(), existing_sources=None, existing_assets=None):
        self.calls = []
        self.fail_verbs = set(fail_verbs)
        self.existing_sources = existing_sources or {}
        self.existing_assets = set(existing_assets or [])

    def _record(self, verb, *args):
        if verb in self.fail_verbs:
            raise RuntimeError(f"central rejected {verb}")
        self.calls.append((verb, *args))

    def declare(self, asset_type, created_by, origin=None, asset_id=None):
        self._record("declare", asset_type, asset_id or created_by)

    def bind_source(self, asset_id, location_uri, tool, revision, published_by):
        self._record("bind_source", asset_id, location_uri)

    def relate(self, from_asset, to_asset, rel_type, binding_mode=None, actor=None):
        self._record("relate", from_asset, to_asset)

    def rename(self, asset_id, new_name, actor, new_taxonomy=None):
        self._record("rename", asset_id, new_name)

    def relocate(self, asset_id, new_location_uri, actor, facet="source", new_revision=None):
        self._record("relocate", asset_id, new_location_uri)

    def bulk_relocate(self, moves):
        self._record("bulk_relocate", len(moves))

    def get_source(self, asset_id):
        return self.existing_sources.get(asset_id)

    def resolve(self, asset_id):
        return {"id": asset_id} if asset_id in self.existing_assets else None


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


def test_replay_dispatches_all_verbs(tmp_path):
    db = open_outbox(str(tmp_path / "o.db"))
    enqueue(db, "declare", {"asset_type": "model", "created_by": "a", "id": "id-1"}, asset_id="id-1")
    enqueue(db, "bind_source", {"asset_id": "id-1", "location_uri": "//d/a.ma", "tool": "maya",
                                "revision": "1", "published_by": "a"}, asset_id="id-1")
    enqueue(db, "relate", {"from_asset": "id-1", "to_asset": "id-2", "rel_type": "DEPENDS_ON", "actor": "a"}, asset_id="id-1")
    enqueue(db, "rename", {"asset_id": "id-1", "new_name": "hero", "actor": "a"}, asset_id="id-1")
    enqueue(db, "relocate", {"asset_id": "id-1", "new_location_uri": "//d/b.ma", "actor": "a"}, asset_id="id-1")
    enqueue(db, "bulk_relocate", {"moves": [{"asset_id": "id-1", "new_location_uri": "//d/c.ma", "actor": "a"}]}, asset_id=None)
    client = FakeClient()
    result = replay_outbox(db, client)
    assert result == {"replayed": 6, "failed": 0, "skipped": 0, "held": 0}
    assert pending_count(db) == 0


def test_replay_skips_already_applied_bind(tmp_path):
    # exact match (same location AND revision) -> our write already landed -> skip
    db = open_outbox(str(tmp_path / "o.db"))
    enqueue(db, "bind_source", {"asset_id": "id-1", "location_uri": "//d/a.ma", "tool": "maya",
                                "revision": "3", "published_by": "a"}, asset_id="id-1")
    client = FakeClient(existing_sources={"id-1": {"location_uri": "//d/a.ma", "tool": "maya", "revision": "3"}})
    result = replay_outbox(db, client)
    assert result["skipped"] == 1 and result["replayed"] == 0
    assert client.calls == []
    assert pending_count(db) == 0


def test_replay_reapplies_bind_when_revision_differs(tmp_path):
    # central at a DIFFERENT revision: opaque revisions have no order, so the queued
    # write is dispatched (lands as a new version), never silently dropped.
    db = open_outbox(str(tmp_path / "o.db"))
    enqueue(db, "bind_source", {"asset_id": "id-1", "location_uri": "//d/a.ma", "tool": "maya",
                                "revision": "3", "published_by": "a"}, asset_id="id-1")
    client = FakeClient(existing_sources={"id-1": {"location_uri": "//d/a.ma", "tool": "maya", "revision": "5"}})
    result = replay_outbox(db, client)
    assert result["replayed"] == 1 and result["skipped"] == 0
    assert client.calls == [("bind_source", "id-1", "//d/a.ma")]


def test_replay_bind_idempotency_is_not_string_ordered(tmp_path):
    # regression: the old check compared revisions as strings, so "9" >= "10" was
    # True and a real pending write got wrongly skipped. Exact-match fixes it.
    assert "9" >= "10"                                   # the trap, made explicit
    db = open_outbox(str(tmp_path / "o.db"))
    enqueue(db, "bind_source", {"asset_id": "id-1", "location_uri": "//d/a.ma", "tool": "maya",
                                "revision": "10", "published_by": "a"}, asset_id="id-1")
    client = FakeClient(existing_sources={"id-1": {"location_uri": "//d/a.ma", "tool": "maya", "revision": "9"}})
    result = replay_outbox(db, client)
    assert result["replayed"] == 1 and result["skipped"] == 0   # NOT skipped


def test_replay_holds_later_entries_for_failed_asset(tmp_path):
    db = open_outbox(str(tmp_path / "o.db"))
    enqueue(db, "rename", {"asset_id": "id-1", "new_name": "x", "actor": "a"}, asset_id="id-1")
    enqueue(db, "relocate", {"asset_id": "id-1", "new_location_uri": "//d/z.ma", "actor": "a"}, asset_id="id-1")
    enqueue(db, "rename", {"asset_id": "id-2", "new_name": "y", "actor": "a"}, asset_id="id-2")
    client = FakeClient(fail_verbs={"rename"})
    result = replay_outbox(db, client)
    assert result["failed"] == 2
    assert result["held"] == 1
    assert ("relocate", "id-1", "//d/z.ma") not in client.calls
    failed = list_failed(db)
    assert all("central rejected" in (f["error"] or "") for f in failed)


class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class _FakeHTTPError(Exception):
    def __init__(self, status_code, text):
        super().__init__(text)
        self.response = _FakeResponse(status_code, text)


def test_replay_treats_duplicate_edge_as_applied(tmp_path):
    # a relate that already reached central (crash between the write and mark_done)
    # comes back as 400 "duplicate edge" -> already-applied, NOT a failure/hold.
    db = open_outbox(str(tmp_path / "o.db"))
    enqueue(db, "relate", {"from_asset": "id-1", "to_asset": "id-2",
                           "rel_type": "DEPENDS_ON", "actor": "a"}, asset_id="id-1")
    enqueue(db, "rename", {"asset_id": "id-1", "new_name": "hero", "actor": "a"}, asset_id="id-1")

    class DupEdgeClient(FakeClient):
        def relate(self, *a, **kw):
            raise _FakeHTTPError(400, "duplicate edge: id-1-DEPENDS_ON->id-2")

    client = DupEdgeClient()
    result = replay_outbox(db, client)
    assert result["skipped"] == 1          # the duplicate relate: treated as applied
    assert result["replayed"] == 1         # the following rename still runs
    assert result["failed"] == 0 and result["held"] == 0
    assert pending_count(db) == 0          # outbox drained, not poisoned


def test_replay_real_relate_failure_still_fails(tmp_path):
    # a non-duplicate error (e.g. 500 / connection) is a genuine failure + hold.
    db = open_outbox(str(tmp_path / "o.db"))
    enqueue(db, "relate", {"from_asset": "id-1", "to_asset": "id-2",
                           "rel_type": "DEPENDS_ON", "actor": "a"}, asset_id="id-1")

    class BrokenClient(FakeClient):
        def relate(self, *a, **kw):
            raise _FakeHTTPError(500, "internal server error")

    result = replay_outbox(db, BrokenClient())
    assert result["failed"] == 1 and result["skipped"] == 0


def test_retry_failed_resets_to_pending(tmp_path):
    db = open_outbox(str(tmp_path / "o.db"))
    eid = enqueue(db, "rename", {"asset_id": "id-1", "new_name": "x", "actor": "a"}, asset_id="id-1")
    mark_failed(db, eid, "boom")
    assert retry_failed(db) == 1
    assert pending_count(db) == 1 and failed_count(db) == 0


import uuid
from assetcore.sdk.hub import PipelineConfig
from assetcore.sdk.offline import HybridClient, open_outbox, pending_count
from assetcore.sdk.replica import open_replica, get_asset


def _pipeline(tmp_path):
    return PipelineConfig(
        central_url="http://central:8080", local_cache=str(tmp_path / "assetcore.db"),
        outbox_path=str(tmp_path / "outbox.db"), config_path=str(tmp_path / "pipeline.toml"),
        scope={"assetcore_project": "MyGame"},
    )


class DownCentral:
    def __getattr__(self, name):
        def boom(*a, **kw):
            raise ConnectionError("central down")
        return boom


class UpCentral:
    def __init__(self):
        self.calls = []
    def ping(self): return True
    def declare(self, asset_type, created_by, origin=None, asset_id=None):
        self.calls.append(("declare", asset_type, asset_id))
        return {"id": asset_id, "asset_type": asset_type}
    def bind_source(self, asset_id, location_uri, tool, revision, actor):
        self.calls.append(("bind_source", asset_id))
        return {"id": asset_id}


def test_offline_declare_queues_and_patches_replica(tmp_path):
    hc = HybridClient(_pipeline(tmp_path), central=DownCentral())
    result = hc.declare("model", "jsmith")
    asset_id = result["id"]
    uuid.UUID(asset_id)                              # client-minted UUID (spec §5.3)
    ob = open_outbox(str(tmp_path / "outbox.db"))
    assert pending_count(ob) == 1                    # queued for replay
    rep = open_replica(str(tmp_path / "assetcore.db"))
    # optimistic local patch, stored in the central resolve() shape
    record = get_asset(rep, asset_id)
    assert record["asset_type"] == "model"
    assert record["meta"]["lifecycle"] == "provisional"


def test_online_declare_goes_to_central_with_client_id(tmp_path):
    central = UpCentral()
    hc = HybridClient(_pipeline(tmp_path), central=central)
    result = hc.declare("model", "jsmith")
    assert central.calls[0][0] == "declare"
    assert central.calls[0][2] == result["id"]       # client id passed through
    ob = open_outbox(str(tmp_path / "outbox.db"))
    assert pending_count(ob) == 0                    # nothing queued


def test_read_falls_back_to_central_when_local_down(tmp_path, monkeypatch):
    class CentralWithRead(UpCentral):
        def resolve(self, asset_id):
            # central ResolveResponse shape (id/meta/identity/source/runtime)
            return {"id": asset_id, "meta": {"id": asset_id, "asset_type": "model"},
                    "identity": None, "source": None, "runtime": None}
    hc = HybridClient(_pipeline(tmp_path), central=CentralWithRead(),
                      os_env={"ASSETCORE_LOCAL_URL": "http://127.0.0.1:1"})  # nothing listens
    out = hc.resolve("a1")
    assert out["id"] == "a1"


def test_health_reports_outbox_counts(tmp_path):
    hc = HybridClient(_pipeline(tmp_path), central=DownCentral(),
                      os_env={"ASSETCORE_LOCAL_URL": "http://127.0.0.1:1"})
    hc.declare("model", "jsmith")
    h = hc.health()
    assert h["central"] == "down" and h["local_reader"] == "down"
    assert h["outbox_pending"] == 1 and h["outbox_failed"] == 0
