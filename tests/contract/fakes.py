"""Fake adapters for the contract suite — no real tool installed.

Two DCC fakes deliberately use *different* stamping mechanisms (an in-memory dict
vs an on-disk sidecar file) so the SAME contract suite proves both. A fake engine
adapter exercises ensure_identity / reconcile. If a fake passes the contract, the
shape is proven before any real Maya/Unreal exists (ARCHITECTURE Part 10).
"""
from __future__ import annotations

import pathlib

from assetcore.sdk.dcc_adapter import DCCAdapter
from assetcore.sdk.engine_adapter import EngineAdapter
from assetcore.sdk.stamping import SidecarStampMixin


class FakeDoc:
    """A stand-in document; hashable by identity so it can key a stamp dict."""

    def __init__(self, location: str, revision: str = "r1") -> None:
        self.location = location
        self.revision = revision


class FakeDCCAdapter(DCCAdapter):
    """Stamps into an in-memory dict; 'documents' are FakeDoc objects."""

    tool = "fake"

    def __init__(self, client) -> None:
        super().__init__(client)
        self._stamps: dict[FakeDoc, str] = {}
        self._n = 0

    def new_doc(self) -> FakeDoc:
        self._n += 1
        return FakeDoc(f"fake://doc/{self._n}.fake", "r1")

    def read_stamp(self, doc: FakeDoc) -> str | None:
        return self._stamps.get(doc)

    def _set_stamp(self, doc: FakeDoc, asset_id: str) -> None:
        self._stamps[doc] = str(asset_id)

    def current_location(self, doc: FakeDoc) -> str:
        return doc.location

    def current_revision(self, doc: FakeDoc) -> str:
        return doc.revision


class FakeSidecarDCCAdapter(SidecarStampMixin, DCCAdapter):
    """Stamps into a `<file>.assetcore` sidecar on disk (the universal fallback)."""

    tool = "fake_sidecar"

    def __init__(self, client, tmp_dir) -> None:
        super().__init__(client)
        self._dir = pathlib.Path(tmp_dir)
        self._n = 0

    def new_doc(self) -> pathlib.Path:
        self._n += 1
        p = self._dir / f"doc_{self._n}.fake"
        p.write_text("payload")
        return p

    def sidecar_path(self, doc) -> pathlib.Path:
        return pathlib.Path(doc)

    # read_stamp / _set_stamp come from SidecarStampMixin; write_stamp + publish
    # + reference from DCCAdapter.
    def current_location(self, doc) -> str:
        return f"file://{doc}"

    def current_revision(self, doc) -> str:
        return "r1"


class FakeEngineAdapter(EngineAdapter):
    """Stamps engine assets in a dict; list_assets returns whatever was added."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self._stamps: dict[str, str] = {}
        self._paths: list[str] = []

    def add_asset(self, asset_path: str) -> None:
        self._paths.append(asset_path)

    def read_stamp(self, asset_path: str) -> str | None:
        return self._stamps.get(asset_path)

    def _set_stamp(self, asset_path: str, asset_id: str) -> None:
        self._stamps[asset_path] = str(asset_id)

    def current_location(self, asset_path: str) -> str:
        return asset_path

    def list_assets(self) -> list[str]:
        return list(self._paths)
