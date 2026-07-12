"""assetcore — identity-first asset management for production pipelines.

The one idea: an asset is an immutable IDENTITY; three sovereign FACETS
(identity/source/runtime) hang off it, bound by a shared UUID. Nothing is
inferred; each authority writes only its own facet. See docs/DESIGN.md.

The obvious entry points:

    from assetcore import AssetcoreClient          # talk to a running service (SDK)
    from assetcore.app import verbs                # drive the verbs in-process
    from assetcore.infra.sqlite_repo import SqliteRepo

`AssetcoreClient` is re-exported here (lazily, so `import assetcore` stays
dependency-light) as the first line most integrations write. The single-file
prototype this grew from is frozen under `examples/prototype/`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.1.0"

__all__ = ["AssetcoreClient", "__version__"]

if TYPE_CHECKING:  # pragma: no cover - typing only
    from assetcore.sdk.client import AssetcoreClient


def __getattr__(name: str):
    # PEP 562 lazy re-export: keeps `import assetcore` from pulling httpx unless the
    # client is actually used.
    if name == "AssetcoreClient":
        from assetcore.sdk.client import AssetcoreClient
        return AssetcoreClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
