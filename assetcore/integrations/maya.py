"""integrations/maya.py — the real Maya DCC integration (L4).

A translator: Maya's vocabulary -> the universal verbs, via the SDK's DCCAdapter.
Identity is stamped into the scene's `fileInfo` (persists in the .ma/.mb across
renames and `p4 move`); the source location/revision come from the Perforce
workspace. Everything tool-agnostic (publish, reference, the stamp-overwrite
guard) is inherited from DCCAdapter — this file is only the four Maya-specific
methods, reached through injectable seams so the adapter is testable headless and
imports cleanly outside Maya (the `maya`/`p4` imports are lazy).

Firewall (Part 8): imports only the SDK, never core/app/infra/service.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Protocol

from assetcore.sdk.dcc_adapter import DCCAdapter

STAMP_KEY = "assetcore_uuid"   # the fileInfo entry that carries identity


# --- the seams: the bits that actually touch Maya / Perforce ---------------
class MayaScene(Protocol):
    def open_or_new(self, path: str) -> None: ...
    def file_info_get(self, key: str) -> str | None: ...
    def file_info_set(self, key: str, value: str) -> None: ...


class MayaVcs(Protocol):
    def depot_path(self, local_path: str) -> str: ...
    def revision(self, local_path: str) -> str: ...


class _RealMayaScene:
    """Wraps maya.cmds (imported lazily — only present inside a Maya session)."""

    def __init__(self) -> None:
        from maya import cmds  # noqa: PLC0415 — must be lazy; absent outside Maya
        self._cmds = cmds
        self._current: str | None = None

    def open_or_new(self, path: str) -> None:
        if path == self._current:
            return   # already the open scene — don't reload and lose in-memory fileInfo
        if os.path.exists(path):
            self._cmds.file(path, open=True, force=True)
        else:
            self._cmds.file(new=True, force=True)
            self._cmds.file(rename=path)
        self._current = path

    def file_info_get(self, key: str) -> str | None:
        vals = self._cmds.fileInfo(key, query=True)
        return vals[0] if vals else None

    def file_info_set(self, key: str, value: str) -> None:
        self._cmds.fileInfo(key, value)
        self._cmds.file(save=True)   # persist the stamp into the .ma so it round-trips


class _RealMayaVcs:
    """Depot path + revision via the `p4` CLI (needs a configured workspace)."""

    def depot_path(self, local_path: str) -> str:
        out = subprocess.run(["p4", "-F", "%depotFile%", "where", local_path],
                             capture_output=True, text=True, check=True).stdout.strip()
        if not out:   # don't silently fall back to a local path that routes to the wrong resolver
            raise RuntimeError(
                f"p4 where returned nothing for {local_path!r}; is it under a P4 workspace?")
        return out.splitlines()[0]

    def revision(self, local_path: str) -> str:
        out = subprocess.run(["p4", "-F", "%change%", "changes", "-m1", local_path],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out.splitlines()[0] if out else "0"


# --- the adapter ------------------------------------------------------------
class MayaAdapter(DCCAdapter):
    tool = "maya"

    def __init__(self, client, scene: MayaScene | None = None,
                 vcs: MayaVcs | None = None) -> None:
        super().__init__(client)
        self._scene = scene if scene is not None else _RealMayaScene()
        self._vcs = vcs if vcs is not None else _RealMayaVcs()
        self._n = 0

    def new_doc(self) -> str:
        """Start a fresh scene (batch-style) and return its path."""
        self._n += 1
        path = os.path.join(tempfile.gettempdir(), f"assetcore_scene_{self._n}.ma")
        self._scene.open_or_new(path)
        return path

    def read_stamp(self, doc) -> str | None:
        self._scene.open_or_new(doc)
        return self._scene.file_info_get(STAMP_KEY)

    def _set_stamp(self, doc, asset_id: str) -> None:
        self._scene.open_or_new(doc)
        self._scene.file_info_set(STAMP_KEY, str(asset_id))

    def current_location(self, doc) -> str:
        return self._vcs.depot_path(doc)

    def current_revision(self, doc) -> str:
        return self._vcs.revision(doc)
