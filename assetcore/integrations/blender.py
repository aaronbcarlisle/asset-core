"""integrations/blender.py — the Blender DCC integration (L4).

The swap test, half 1. A whole new authoring tool in four methods: identity lives
in a scene custom property (`bpy.context.scene["assetcore_uuid"]`, persisted in the
.blend), and source location is a git URI. Nothing below L4 changes — Blender emits
the same universal verbs Maya does. If this felt like a weekend, the thesis holds.

Firewall (Part 8): imports only the SDK, never core/app/infra/service.
"""
from __future__ import annotations

import subprocess
from typing import Protocol

from assetcore.sdk.dcc_adapter import DCCAdapter

STAMP_KEY = "assetcore_uuid"   # scene custom property carrying identity


class BlenderScene(Protocol):
    def open_or_new(self, path: str) -> None: ...
    def prop_get(self, key: str) -> str | None: ...
    def prop_set(self, key: str, value: str) -> None: ...


class BlenderVcs(Protocol):
    def location(self, local_path: str) -> str: ...
    def revision(self, local_path: str) -> str: ...


class _RealBlenderScene:
    """Wraps bpy (imported lazily — only inside Blender)."""

    def __init__(self) -> None:
        import bpy  # noqa: PLC0415 — must be lazy; absent outside Blender
        self._bpy = bpy

    def open_or_new(self, path: str) -> None:
        import os
        if os.path.exists(path):
            self._bpy.ops.wm.open_mainfile(filepath=path)
        else:
            self._bpy.ops.wm.read_homefile(use_empty=True)
            self._bpy.ops.wm.save_as_mainfile(filepath=path)

    def prop_get(self, key: str) -> str | None:
        return self._bpy.context.scene.get(key)

    def prop_set(self, key: str, value: str) -> None:
        self._bpy.context.scene[key] = value


class _RealBlenderVcs:
    """git-based source location: git://<remote>@<sha>/<path>."""

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()

    def location(self, local_path: str) -> str:
        remote = self._git("config", "--get", "remote.origin.url") or "local"
        return f"git://{remote}@{self.revision(local_path)}/{local_path}"

    def revision(self, local_path: str) -> str:
        return self._git("rev-parse", "HEAD")


class BlenderAdapter(DCCAdapter):
    tool = "blender"

    def __init__(self, client, scene: BlenderScene | None = None,
                 vcs: BlenderVcs | None = None) -> None:
        super().__init__(client)
        self._scene = scene if scene is not None else _RealBlenderScene()
        self._vcs = vcs if vcs is not None else _RealBlenderVcs()
        self._n = 0

    def new_doc(self) -> str:
        self._n += 1
        path = f"/work/asset_{self._n}.blend"
        self._scene.open_or_new(path)
        return path

    def read_stamp(self, doc) -> str | None:
        self._scene.open_or_new(doc)
        return self._scene.prop_get(STAMP_KEY)

    def _set_stamp(self, doc, asset_id: str) -> None:
        self._scene.open_or_new(doc)
        self._scene.prop_set(STAMP_KEY, str(asset_id))

    def current_location(self, doc) -> str:
        return self._vcs.location(doc)

    def current_revision(self, doc) -> str:
        return self._vcs.revision(doc)
