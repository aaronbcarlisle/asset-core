"""DCCAdapter — the base class for authoring tools (Maya, Blender, Substance, ...).

Owns the SOURCE facet for assets it authors. A concrete adapter fills in four
tool-specific methods (how to read/write the stamp, where the doc lives, what
revision it is) and inherits the tool-agnostic verbs: publish (stamp-if-needed +
bind_source) and reference (relate DEPENDS_ON + resolve). Everything above those
four methods is identical across every DCC — that is the "weekend, not a quarter"
promise (ARCHITECTURE Part 4).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from assetcore.sdk.client import AssetcoreClient
from assetcore.sdk.stamping import guard_overwrite


class DCCAdapter(ABC):
    tool: str = "unknown"          # subclass sets the tool label ('maya', 'blender', ...)

    def __init__(self, client: AssetcoreClient) -> None:
        self.client = client

    # --- the four tool-specific methods every DCC adapter implements ---
    @abstractmethod
    def read_stamp(self, doc) -> str | None:
        """Read the identity stamped into this document, or None if unstamped."""

    @abstractmethod
    def _set_stamp(self, doc, asset_id: str) -> None:
        """Write the stamp into the document (raw; the guard lives in write_stamp)."""

    @abstractmethod
    def current_location(self, doc) -> str:
        """The opaque location_uri of this document (//depot/..., git://..., ...)."""

    @abstractmethod
    def current_revision(self, doc) -> str:
        """The VCS revision this document is at (P4 CL, git sha, ...)."""

    # --- provided by the SDK, tool-agnostic ---
    def write_stamp(self, doc, asset_id: str) -> None:
        """Stamp identity, refusing to replace a different existing one."""
        guard_overwrite(self.read_stamp(doc), asset_id)
        self._set_stamp(doc, asset_id)

    def publish(self, doc, asset_type: str, artist: str) -> str:
        """Stamp-if-needed, then bind the source facet. Returns the asset id.

        First publish mints + stamps; every later publish reads the existing stamp,
        so identity is stable and only the source version advances.
        """
        asset_id = self.read_stamp(doc)
        if asset_id is None:
            asset_id = self.client.declare(asset_type, artist)
            self.write_stamp(doc, asset_id)
        self.client.bind_source(asset_id, self.current_location(doc), self.tool,
                                self.current_revision(doc), artist)
        return asset_id

    def reference(self, doc, dependency_id: str, mode: str = "float") -> dict | None:
        """Relate this doc's asset as DEPENDS_ON `dependency_id`, return what to load."""
        consumer_id = self.read_stamp(doc)
        if consumer_id is None:
            raise ValueError("cannot reference from an unpublished document (no stamp)")
        self.client.relate(consumer_id, dependency_id, "DEPENDS_ON", binding_mode=mode)
        return self.client.resolve_dependency(consumer_id, dependency_id)
