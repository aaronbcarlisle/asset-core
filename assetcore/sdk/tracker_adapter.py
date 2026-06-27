"""TrackerAdapter — the base class for production trackers (ShotGrid, ftrack, ...).

A VIEW over the IDENTITY facet: it mirrors identity outward and pulls tracker
edits back in as claim/rename. It NEVER drives paths or owns source/runtime — a
tracker is a lens, not an authority over where bytes live (ARCHITECTURE Part 4.1;
anti-pattern: a tracker path-driving the pipeline). Its full contract suite lands
with Phase 7; the base shape is defined here so the SDK is complete.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from assetcore.sdk.client import AssetcoreClient


class TrackerAdapter(ABC):
    def __init__(self, client: AssetcoreClient) -> None:
        self.client = client

    @abstractmethod
    def push_identity(self, asset_id: str, fields: dict) -> None:
        """Mirror the identity facet outward into the tracker."""

    @abstractmethod
    def pull_identity(self, external_id: str) -> dict:
        """Read a tracker record (to be applied via claim/rename)."""

    # --- provided by the SDK ---
    def mirror(self, asset_id: str) -> None:
        """Push the current identity facet to the tracker (read via resolve)."""
        identity = self.client.resolve(asset_id).get("identity") or {}
        self.push_identity(asset_id, identity)

    def apply(self, asset_id: str, external_id: str, actor: str) -> None:
        """Pull tracker edits and apply them as a rename (identity facet only)."""
        record = self.pull_identity(external_id)
        self.client.rename(asset_id, record["display_name"], actor,
                           record.get("taxonomy"))
