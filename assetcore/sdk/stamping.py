"""The stamping protocol — how identity lives inside a tool's document.

Two pieces every adapter reuses:
  * guard_overwrite — the never-strip-identity invariant (ARCHITECTURE 7.3 #3),
    enforced generically so no adapter can forget it. NOTE: this duplicates the
    one-liner in core.rules.can_overwrite_stamp by design — the SDK firewall
    forbids importing the core, so the guard is restated at the HTTP boundary.
  * SidecarStampMixin — identity in a `<file>.assetcore` sidecar, the fallback
    for tools/formats with no metadata slot (raw images, FBX, ...), so no asset
    type is structurally un-stampable.
"""
from __future__ import annotations

import pathlib


class StampConflict(Exception):
    """Raised when a write would replace a *different* existing identity stamp."""


def guard_overwrite(existing: str | None, incoming: str) -> None:
    """Never strip identity: a different existing stamp may not be overwritten."""
    if existing is not None and existing != incoming:
        raise StampConflict(
            f"refusing to overwrite stamp {existing!r} with {incoming!r}")


class SidecarStampMixin:
    """Stamp into a `<file>.assetcore` sidecar. Adapter supplies `sidecar_path(doc)`.

    Provides read_stamp / _set_stamp in terms of that path, so a sidecar-backed
    adapter only has to say *where* its document lives on disk.
    """

    SIDECAR_SUFFIX = ".assetcore"

    def sidecar_path(self, doc) -> pathlib.Path:   # pragma: no cover - overridden
        raise NotImplementedError("adapter must provide sidecar_path(doc)")

    def _sidecar(self, doc) -> pathlib.Path:
        p = self.sidecar_path(doc)
        return p if p.name.endswith(self.SIDECAR_SUFFIX) else p.with_name(p.name + self.SIDECAR_SUFFIX)

    def read_stamp(self, doc) -> str | None:
        sc = self._sidecar(doc)
        if not sc.exists():
            return None
        return sc.read_text().strip() or None   # a blank sidecar means "unstamped", not ""

    def _set_stamp(self, doc, asset_id: str) -> None:
        self._sidecar(doc).write_text(str(asset_id))
