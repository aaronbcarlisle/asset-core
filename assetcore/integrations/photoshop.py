"""integrations/photoshop.py — the real Photoshop integration (L4).

Concept art is the FRONT of the pipeline (everything downstream DEPENDS_ON /
DERIVED_FROM a concept), and Photoshop authors it. A translator: Photoshop's
vocabulary -> the universal verbs, via the SDK's DCCAdapter. Identity is stamped
into the `.psd`'s XMP metadata (a custom assetcore namespace — the Photoshop analog
of Maya's fileInfo / Max's fileProperties; it persists in the file across renames
and `p4 move`); the source location/revision come from the Perforce workspace.

Everything tool-agnostic (publish, reference, the stamp-overwrite guard) is
inherited from DCCAdapter — this file is only the four Photoshop-specific methods,
reached through injectable seams so the adapter is testable headless and imports
cleanly without Photoshop (the `photoshop`/`p4` access is lazy). The seam shape is
identical to Maya's/Max's, so it passes the SAME DCC contract — no change below L4.

Firewall (Part 8): imports only the SDK, never core/app/infra/service.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Protocol

from assetcore.sdk.dcc_adapter import DCCAdapter

STAMP_KEY = "assetcore_uuid"               # the XMP property that carries identity
_XMP_NS = "http://assetcore.dev/ns/1.0/"   # custom XMP namespace for assetcore metadata


# --- the seams: the bits that actually touch Photoshop / Perforce ----------
class PhotoshopDoc(Protocol):
    def open_or_new(self, path: str) -> None: ...
    def file_info_get(self, key: str) -> str | None: ...
    def file_info_set(self, key: str, value: str) -> None: ...


class PhotoshopVcs(Protocol):
    def depot_path(self, local_path: str) -> str: ...
    def revision(self, local_path: str) -> str: ...


# JSX run via app.doJavaScript — XMP custom-namespace get/set is the scriptable way
# to store durable custom metadata in a .psd (the AdobeXMPScript external object).
# Every interpolated value is json.dumps()'d into a safe JS string literal (incl. the
# quotes), so backslashes in Windows paths or quotes/backslashes in a value can't
# break the JS — never hand-quote interpolated input.
_JSX_SET = """
(function(key, val) {
  if (ExternalObject.AdobeXMPScript == undefined)
    ExternalObject.AdobeXMPScript = new ExternalObject("lib:AdobeXMPScript");
  var xmp = new XMPMeta(activeDocument.xmpMetadata.rawData);
  XMPMeta.registerNamespace(%(ns)s, "assetcore");
  xmp.setProperty(%(ns)s, key, val);
  activeDocument.xmpMetadata.rawData = xmp.serialize();
  activeDocument.save();
})(%(key)s, %(val)s);
"""
_JSX_GET = """
(function(key) {
  if (ExternalObject.AdobeXMPScript == undefined)
    ExternalObject.AdobeXMPScript = new ExternalObject("lib:AdobeXMPScript");
  var xmp = new XMPMeta(activeDocument.xmpMetadata.rawData);
  var p = xmp.getProperty(%(ns)s, key);
  return p ? String(p) : "";
})(%(key)s);
"""


class _RealPhotoshopDoc:
    """Wraps the Photoshop COM app (photoshop-python-api; imported lazily)."""

    def __init__(self) -> None:
        import photoshop.api as ps  # noqa: PLC0415 — lazy; absent without Photoshop
        self._ps = ps
        self._app = ps.Application()
        self._current: str | None = None

    def open_or_new(self, path: str) -> None:
        if path == self._current:
            return
        if os.path.exists(path):
            self._app.open(path)
        else:
            doc = self._app.documents.add(512, 512, 72, os.path.basename(path))
            doc.saveAs(path, self._ps.PhotoshopSaveOptions(), asCopy=False)
        self._current = path

    def file_info_get(self, key: str) -> str | None:
        out = self._app.doJavaScript(
            _JSX_GET % {"ns": json.dumps(_XMP_NS), "key": json.dumps(key)})
        return out or None

    def file_info_set(self, key: str, value: str) -> None:
        self._app.doJavaScript(_JSX_SET % {
            "ns": json.dumps(_XMP_NS), "key": json.dumps(key), "val": json.dumps(value)})


class _RealPhotoshopVcs:
    """Depot path + revision via the `p4` CLI. Same -ztag seam as Maya/Max (the
    global `-F "%field%"` formatter is unreliable on a real server)."""

    def depot_path(self, local_path: str) -> str:
        out = subprocess.run(["p4", "-ztag", "-F", "%depotFile%", "where", local_path],
                             capture_output=True, text=True, check=True).stdout.strip()
        if not out:
            raise RuntimeError(
                f"p4 where returned nothing for {local_path!r}; is it under a P4 workspace?")
        return out.splitlines()[0]

    def revision(self, local_path: str) -> str:
        out = subprocess.run(["p4", "-ztag", "-F", "%change%", "changes", "-m1", local_path],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out.splitlines()[0] if out else "0"


# --- the adapter ------------------------------------------------------------
class PhotoshopAdapter(DCCAdapter):
    tool = "photoshop"

    def __init__(self, client, doc: PhotoshopDoc | None = None,
                 vcs: PhotoshopVcs | None = None) -> None:
        super().__init__(client)
        self._doc = doc if doc is not None else _RealPhotoshopDoc()
        self._vcs = vcs if vcs is not None else _RealPhotoshopVcs()
        self._n = 0

    def new_doc(self) -> str:
        self._n += 1
        path = os.path.join(tempfile.gettempdir(), f"assetcore_concept_{self._n}.psd")
        self._doc.open_or_new(path)
        return path

    def read_stamp(self, doc) -> str | None:
        self._doc.open_or_new(doc)
        return self._doc.file_info_get(STAMP_KEY)

    def _set_stamp(self, doc, asset_id: str) -> None:
        self._doc.open_or_new(doc)
        self._doc.file_info_set(STAMP_KEY, str(asset_id))

    def current_location(self, doc) -> str:
        return self._vcs.depot_path(doc)

    def current_revision(self, doc) -> str:
        return self._vcs.revision(doc)
