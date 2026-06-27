"""integrations/max.py — the real 3ds Max DCC integration (L4).

A translator: 3ds Max's vocabulary -> the universal verbs, via the SDK's DCCAdapter.
Identity is stamped into the scene's custom `fileProperties` (persists in the .max
across renames and `p4 move`); the source location/revision come from the Perforce
workspace. Everything tool-agnostic (publish, reference, the stamp-overwrite guard)
is inherited from DCCAdapter — this file is only the four Max-specific methods,
reached through injectable seams so the adapter is testable headless and imports
cleanly outside Max (the `pymxs`/`p4` access is lazy).

The seam shape is identical to Maya's (open_or_new / file_info_get / file_info_set
+ depot_path / revision), which is exactly why MaxAdapter passes the SAME DCC
contract as Maya/Blender/Substance with no change below L4 — "add a tool = a
weekend adapter" (ARCHITECTURE Part 4 / Part 10).

Firewall (Part 8): imports only the SDK, never core/app/infra/service.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Protocol

from assetcore.sdk.dcc_adapter import DCCAdapter

STAMP_KEY = "assetcore_uuid"   # the custom fileProperty that carries identity


# --- the seams: the bits that actually touch 3ds Max / Perforce ------------
class MaxScene(Protocol):
    def open_or_new(self, path: str) -> None: ...
    def file_info_get(self, key: str) -> str | None: ...
    def file_info_set(self, key: str, value: str) -> None: ...


class MaxVcs(Protocol):
    def depot_path(self, local_path: str) -> str: ...
    def revision(self, local_path: str) -> str: ...


class _RealMaxScene:
    """Wraps pymxs (imported lazily — only present inside a 3ds Max runtime)."""

    def __init__(self) -> None:
        from pymxs import runtime as rt  # noqa: PLC0415 — lazy; absent outside Max
        self._rt = rt
        self._custom = rt.Name("custom")   # the #custom fileProperties bucket
        self._current: str | None = None

    def open_or_new(self, path: str) -> None:
        if path == self._current:
            return   # already the open scene — don't reload and lose in-memory props
        if os.path.exists(path):
            self._rt.loadMaxFile(path, quiet=True)
        else:
            self._rt.resetMaxFile(self._rt.Name("noPrompt"))
        self._current = path

    def file_info_get(self, key: str) -> str | None:
        idx = self._rt.fileProperties.findProperty(self._custom, key)
        if not idx:   # findProperty returns a 1-based index, 0 when absent
            return None
        return self._rt.fileProperties.getPropertyValue(self._custom, idx)

    def file_info_set(self, key: str, value: str) -> None:
        # addProperty doesn't replace, so clear any existing entry first
        if self._rt.fileProperties.findProperty(self._custom, key):
            self._rt.fileProperties.deleteProperty(self._custom, key)
        self._rt.fileProperties.addProperty(self._custom, key, value)
        self._rt.saveMaxFile(self._current)   # persist the stamp into the .max


class _RealMaxVcs:
    """Depot path + revision via the `p4` CLI (needs a configured workspace).

    Same p4 seam as Maya's; `-ztag` is required because the global `-F "%field%"`
    formatter is unreliable on a real server (`%path%` empty, `%change%` -> "Change
    N"). A shared PerforceVcs is a natural future extraction.
    """

    def depot_path(self, local_path: str) -> str:
        out = subprocess.run(["p4", "-ztag", "-F", "%depotFile%", "where", local_path],
                             capture_output=True, text=True, check=True).stdout.strip()
        if not out:   # don't silently fall back to a local path -> wrong resolver
            raise RuntimeError(
                f"p4 where returned nothing for {local_path!r}; is it under a P4 workspace?")
        return out.splitlines()[0]

    def revision(self, local_path: str) -> str:
        out = subprocess.run(["p4", "-ztag", "-F", "%change%", "changes", "-m1", local_path],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out.splitlines()[0] if out else "0"


# --- the adapter ------------------------------------------------------------
class MaxAdapter(DCCAdapter):
    tool = "max"

    def __init__(self, client, scene: MaxScene | None = None,
                 vcs: MaxVcs | None = None) -> None:
        super().__init__(client)
        self._scene = scene if scene is not None else _RealMaxScene()
        self._vcs = vcs if vcs is not None else _RealMaxVcs()
        self._n = 0

    def new_doc(self) -> str:
        """Start a fresh scene (batch-style) and return its path."""
        self._n += 1
        path = os.path.join(tempfile.gettempdir(), f"assetcore_scene_{self._n}.max")
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
