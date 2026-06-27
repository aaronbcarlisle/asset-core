"""integrations/substance.py — the Substance Designer DCC integration (L4).

The swap test, half 2. Another authoring tool in four methods: identity lives in
the package's user metadata, source location is the exported asset's depot path.
Same DCCAdapter, same verbs — a third tool with zero changes below L4.

Firewall (Part 8): imports only the SDK, never core/app/infra/service.
"""
from __future__ import annotations

from typing import Protocol

from assetcore.sdk.dcc_adapter import DCCAdapter

STAMP_KEY = "assetcore_uuid"   # package user-metadata key carrying identity


class SubstancePackage(Protocol):
    def open_or_new(self, path: str) -> None: ...
    def metadata_get(self, key: str) -> str | None: ...
    def metadata_set(self, key: str, value: str) -> None: ...


class SubstanceVcs(Protocol):
    def location(self, local_path: str) -> str: ...
    def revision(self, local_path: str) -> str: ...


class _RealSubstancePackage:
    """Wraps the Substance Designer `sd` API (imported lazily)."""

    def __init__(self) -> None:
        import sd  # noqa: PLC0415 — must be lazy; absent outside Substance
        self._sd = sd
        self._ctx = sd.getContext()

    def _current_package(self):
        app = self._ctx.getSDApplication()
        return app.getPackageMgr().getUserPackages()[-1]

    def open_or_new(self, path: str) -> None:
        mgr = self._ctx.getSDApplication().getPackageMgr()
        mgr.loadUserPackage(path) if _exists(path) else mgr.newUserPackage()

    def metadata_get(self, key: str) -> str | None:
        md = self._current_package().getMetadataDict()
        prop = md.getPropertyValueFromId(key)
        return prop.get() if prop is not None else None

    def metadata_set(self, key: str, value: str) -> None:
        from sd.api.sdvaluestring import SDValueString
        self._current_package().getMetadataDict().setPropertyValueFromId(
            key, SDValueString.sNew(value))


def _exists(path: str) -> bool:
    import os
    return os.path.exists(path)


class _RealSubstanceVcs:
    def location(self, local_path: str) -> str:
        return local_path           # site wires the depot/export mapping here

    def revision(self, local_path: str) -> str:
        return "0"


class SubstanceAdapter(DCCAdapter):
    tool = "substance"

    def __init__(self, client, package: SubstancePackage | None = None,
                 vcs: SubstanceVcs | None = None) -> None:
        super().__init__(client)
        self._pkg = package if package is not None else _RealSubstancePackage()
        self._vcs = vcs if vcs is not None else _RealSubstanceVcs()
        self._n = 0

    def new_doc(self) -> str:
        self._n += 1
        path = f"/work/material_{self._n}.sbs"
        self._pkg.open_or_new(path)
        return path

    def read_stamp(self, doc) -> str | None:
        self._pkg.open_or_new(doc)
        return self._pkg.metadata_get(STAMP_KEY)

    def _set_stamp(self, doc, asset_id: str) -> None:
        self._pkg.open_or_new(doc)
        self._pkg.metadata_set(STAMP_KEY, str(asset_id))

    def current_location(self, doc) -> str:
        return self._vcs.location(doc)

    def current_revision(self, doc) -> str:
        return self._vcs.revision(doc)
