"""Deprecated shim — the hub CLI moved to `assetcore.sdk.hub_cli` (it speaks only
to the SDK, so it belongs in L3, not L4).

Kept for one release so existing imports (`assetcore.integrations.hub_bridge`)
keep working. Import from `assetcore.sdk.hub_cli` instead.
"""
from __future__ import annotations

from assetcore.sdk.hub_cli import main  # noqa: F401 — re-export for back-compat

__all__ = ["main"]
