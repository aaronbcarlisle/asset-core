"""integrations/unreal.py — the real Unreal engine integration (L4).

A translator: Unreal's vocabulary -> the universal verbs, via the SDK's
EngineAdapter. Identity is stamped into the asset's metadata tag (persists in the
.uasset across in-editor renames/moves); the runtime location is the engine
address ('/Game/...'). ensure_identity (mint-if-editor-native) and reconcile
(walk + bind_runtime) are inherited; this file is only the Unreal-specific access,
behind an injectable seam so it's testable headless and imports cleanly outside
the editor (the `unreal` import is lazy).

Firewall (Part 8): imports only the SDK, never core/app/infra/service.
"""
from __future__ import annotations

from typing import Protocol

from assetcore.sdk.engine_adapter import EngineAdapter

STAMP_TAG = "assetcore_uuid"   # the metadata tag that carries identity


class UnrealEditor(Protocol):
    def get_metadata(self, asset_path: str, tag: str) -> str | None: ...
    def set_metadata(self, asset_path: str, tag: str, value: str) -> None: ...
    def save(self, asset_path: str) -> None: ...
    def list_assets(self) -> list[str]: ...


class _RealUnrealEditor:
    """Wraps the `unreal` module (imported lazily — only inside the editor)."""

    def __init__(self, root: str = "/Game") -> None:
        import unreal  # noqa: PLC0415 — must be lazy; absent outside the editor
        self._u = unreal
        self._root = root

    def get_metadata(self, asset_path: str, tag: str) -> str | None:
        obj = self._u.EditorAssetLibrary.load_asset(asset_path)
        return self._u.EditorAssetLibrary.get_metadata_tag(obj, tag) or None

    def set_metadata(self, asset_path: str, tag: str, value: str) -> None:
        obj = self._u.EditorAssetLibrary.load_asset(asset_path)
        self._u.EditorAssetLibrary.set_metadata_tag(obj, tag, value)

    def save(self, asset_path: str) -> None:
        self._u.EditorAssetLibrary.save_asset(asset_path)

    def list_assets(self) -> list[str]:
        return list(self._u.EditorAssetLibrary.list_assets(self._root, recursive=True))


class UnrealAdapter(EngineAdapter):
    def __init__(self, client, editor: UnrealEditor | None = None) -> None:
        super().__init__(client)
        self._editor = editor if editor is not None else _RealUnrealEditor()

    def read_stamp(self, asset_path: str) -> str | None:
        return self._editor.get_metadata(asset_path, STAMP_TAG)

    def _set_stamp(self, asset_path: str, asset_id: str) -> None:
        self._editor.set_metadata(asset_path, STAMP_TAG, str(asset_id))
        self._editor.save(asset_path)

    def current_location(self, asset_path: str) -> str:
        return asset_path

    def list_assets(self) -> list[str]:
        return self._editor.list_assets()
