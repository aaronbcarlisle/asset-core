"""EngineAdapter — the base class for runtime targets (Unreal, Unity, Godot, ...).

Owns the RUNTIME facet. A concrete adapter says how to read/write the stamp on an
engine asset and how to list assets; it inherits ensure_identity (stamp an
editor-native asset that has none) and reconcile (walk the project and bind_runtime
for each stamped asset under a build). ARCHITECTURE Part 4.1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from assetcore.sdk.client import AssetcoreClient
from assetcore.sdk.stamping import guard_overwrite


class EngineAdapter(ABC):
    def __init__(self, client: AssetcoreClient) -> None:
        self.client = client

    # --- engine-specific ---
    @abstractmethod
    def read_stamp(self, asset_path: str) -> str | None: ...

    @abstractmethod
    def _set_stamp(self, asset_path: str, asset_id: str) -> None: ...

    @abstractmethod
    def current_location(self, asset_path: str) -> str:
        """The engine-internal address of this asset ('/Game/...')."""

    @abstractmethod
    def list_assets(self) -> list[str]:
        """Every engine asset path the reconcile walk should consider."""

    # --- provided by the SDK ---
    def write_stamp(self, asset_path: str, asset_id: str) -> None:
        guard_overwrite(self.read_stamp(asset_path), asset_id)
        self._set_stamp(asset_path, asset_id)

    def ensure_identity(self, asset_path: str, asset_type: str, created_by: str = "engine") -> str:
        """Return this asset's identity, minting + stamping it if editor-native."""
        asset_id = self.read_stamp(asset_path)
        if asset_id is None:
            asset_id = self.client.declare(asset_type, created_by)
            self.write_stamp(asset_path, asset_id)
        return asset_id

    def reconcile(self, build_id: str) -> dict[str, int]:
        """Walk listed assets; bind_runtime each stamped one. Returns path->version."""
        bound: dict[str, int] = {}
        for path in self.list_assets():
            asset_id = self.read_stamp(path)
            if asset_id is None:
                continue                      # unstamped: not ours to bind (never guess)
            bound[path] = self.client.bind_runtime(asset_id, self.current_location(path), build_id)
        return bound

    def on_asset_saved(self, asset_path: str, build_id: str,
                       asset_type: str = "engine_asset") -> int:
        """Event-driven reconcile: bind one asset the instant the editor saves it.

        Where the engine exposes a save hook, this replaces waiting for the next
        periodic reconcile — the runtime facet goes from eventually- to immediately-
        consistent for that asset (ARCHITECTURE Part 7.2 / Phase 8). Mints identity
        first if the asset is editor-native.
        """
        asset_id = self.ensure_identity(asset_path, asset_type)
        return self.client.bind_runtime(asset_id, self.current_location(asset_path), build_id)
