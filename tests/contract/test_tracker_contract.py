"""The Tracker contract (deferred from Phase 4) — a tracker is a VIEW.

Proves ShotGridAdapter mirrors identity outward and applies tracker edits as
rename, and — the load-bearing invariant — that it touches ONLY identity verbs.
A recording client asserts the tracker never calls bind_source/bind_runtime/
relate/set_binding, i.e. it never path-drives the pipeline.
"""
import pytest

from assetcore.integrations.shotgrid import ShotGridAdapter


class FakeShotGridSite:
    """In-memory stand-in for a ShotGrid site (the upsert/get seam)."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self._by_asset: dict[str, str] = {}
        self._n = 0

    def upsert(self, asset_id: str, fields: dict) -> None:
        ext = self._by_asset.get(asset_id)
        if ext is None:
            self._n += 1
            ext = f"sg-{self._n}"
            self._by_asset[asset_id] = ext
        self.records[ext] = {"display_name": fields.get("display_name"),
                             "taxonomy": fields.get("taxonomy"), "asset_id": asset_id}

    def get(self, external_id: str) -> dict:
        return self.records[external_id]

    # test helpers
    def external_id_for(self, asset_id: str) -> str:
        return self._by_asset[asset_id]

    def edit(self, external_id: str, *, display_name=None, taxonomy=None) -> None:
        if display_name is not None:
            self.records[external_id]["display_name"] = display_name
        if taxonomy is not None:
            self.records[external_id]["taxonomy"] = taxonomy


class RecordingClient:
    """Wraps an AssetcoreClient, recording which verb methods get called."""

    _VERBS = {"declare", "claim", "rename", "bind_source", "bind_runtime",
              "relate", "set_binding", "resolve", "resolve_dependency",
              "used_by", "lineage", "find_similar"}

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name in self._VERBS and callable(attr):
            def wrapper(*a, **k):
                self.calls.append(name)
                return attr(*a, **k)
            return wrapper
        return attr


@pytest.fixture
def authored(make_client):
    """An active asset to mirror, plus an artist client to set up source facets."""
    artist = make_client("artist-token")
    aid = artist.declare("prop", "amy")
    artist.bind_source(aid, "//depot/barrel.ma", "maya", "4101", "amy")
    make_client("prod-token").claim(aid, "Barrel", "props/barrel", "pat")
    return aid


def test_mirror_pushes_identity_to_tracker(make_client, authored):
    site = FakeShotGridSite()
    tracker = ShotGridAdapter(make_client("prod-token"), site)
    tracker.mirror(authored)
    ext = site.external_id_for(authored)
    assert site.get(ext)["display_name"] == "Barrel"


def test_apply_pulls_tracker_edit_as_rename(make_client, authored):
    site = FakeShotGridSite()
    prod = make_client("prod-token")
    tracker = ShotGridAdapter(prod, site)
    tracker.mirror(authored)
    ext = site.external_id_for(authored)

    site.edit(ext, display_name="Barrel, Renamed In ShotGrid")   # a tracker-side edit
    tracker.apply(authored, ext, actor="pat")

    resolved = prod.resolve(authored)
    assert resolved["identity"]["display_name"] == "Barrel, Renamed In ShotGrid"
    assert resolved["source"]["location_uri"].endswith("barrel.ma")   # source untouched


def test_tracker_is_a_view_only_touches_identity_verbs(make_client, authored):
    site = FakeShotGridSite()
    recorder = RecordingClient(make_client("prod-token"))
    tracker = ShotGridAdapter(recorder, site)

    tracker.mirror(authored)
    site.edit(site.external_id_for(authored), display_name="X")
    tracker.apply(authored, site.external_id_for(authored), actor="pat")

    forbidden = {"bind_source", "bind_runtime", "relate", "set_binding", "declare"}
    assert not (set(recorder.calls) & forbidden), f"tracker path-drove: {recorder.calls}"
    assert set(recorder.calls) <= {"resolve", "rename"}
