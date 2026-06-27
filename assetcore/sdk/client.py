"""AssetcoreClient — the thin HTTP wrapper every integration calls.

One method per verb, over the L2 API. Returns JSON (dicts / string ids), never
core entities — the SDK only knows the HTTP contract. An httpx.Client is injected
(so tests can pass a FastAPI TestClient, and production passes a real client at a
base_url); auth is the per-authority token sent on every request.

If the client can't express something an integration needs, the *client* grows a
method — an integration never reaches past it into the service internals
(ARCHITECTURE Part 11, anti-pattern #3).
"""
from __future__ import annotations

from typing import Any

import httpx


class AssetcoreClient:
    def __init__(self, token: str, base_url: str = "http://127.0.0.1:8000",
                 http: httpx.Client | None = None) -> None:
        self.token = token
        # own (and therefore close) the client only when we created it; an injected
        # client (e.g. a TestClient) is the caller's to manage.
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(base_url=base_url)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "AssetcoreClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Assetcore-Token": self.token}

    def _post(self, path: str, json: dict | None = None) -> httpx.Response:
        r = self._http.post(path, json=json, headers=self._headers)
        r.raise_for_status()
        return r

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        r = self._http.get(path, params=params, headers=self._headers)
        r.raise_for_status()
        return r

    # --- verbs ---
    def declare(self, asset_type: str, created_by: str, origin: dict | None = None) -> str:
        body: dict[str, Any] = {"asset_type": asset_type, "created_by": created_by}
        if origin is not None:
            body["origin"] = origin
        return self._post("/assets", body).json()["id"]

    def claim(self, asset_id: str, display_name: str, taxonomy: str, actor: str,
              attributes: dict | None = None) -> None:
        self._post(f"/assets/{asset_id}/claim", {
            "display_name": display_name, "taxonomy": taxonomy, "actor": actor,
            "attributes": attributes or {},
        })

    def rename(self, asset_id: str, new_name: str, actor: str,
               new_taxonomy: str | None = None) -> None:
        self._post(f"/assets/{asset_id}/rename",
                   {"new_name": new_name, "actor": actor, "new_taxonomy": new_taxonomy})

    def bind_source(self, asset_id: str, location_uri: str, tool: str, revision: str,
                    published_by: str) -> int:
        return self._post(f"/assets/{asset_id}/source", {
            "location_uri": location_uri, "tool": tool, "revision": revision,
            "published_by": published_by,
        }).json()["version"]

    def bind_runtime(self, asset_id: str, location_uri: str, build_id: str) -> int:
        return self._post(f"/assets/{asset_id}/runtime",
                          {"location_uri": location_uri, "build_id": build_id}).json()["version"]

    def relate(self, from_asset: str, to_asset: str, rel_type: str,
               binding_mode: str | None = None, pinned_version: int | None = None,
               actor: str | None = None) -> None:
        self._post("/relate", {
            "from_asset": from_asset, "to_asset": to_asset, "rel_type": rel_type,
            "binding_mode": binding_mode, "pinned_version": pinned_version, "actor": actor,
        })

    def set_binding(self, from_asset: str, to_asset: str, binding_mode: str,
                    pinned_version: int | None = None) -> None:
        self._post("/set_binding", {
            "from_asset": from_asset, "to_asset": to_asset,
            "binding_mode": binding_mode, "pinned_version": pinned_version,
        })

    def resolve(self, asset_id: str) -> dict:
        return self._get(f"/assets/{asset_id}").json()

    def resolve_dependency(self, from_asset: str, to_asset: str) -> dict | None:
        return self._get("/dependency", {"frm": from_asset, "to": to_asset}).json()

    def used_by(self, asset_id: str) -> list[dict]:
        return self._get(f"/assets/{asset_id}/used_by").json()

    def lineage(self, asset_id: str) -> list[dict]:
        return self._get(f"/assets/{asset_id}/lineage").json()

    def find_similar(self, name: str, asset_type: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"name": name}
        if asset_type is not None:
            params["asset_type"] = asset_type
        return self._get("/similar", params).json()

    def backfill_worklist(self) -> list[dict]:
        return self._get("/worklist/provisional").json()

    def floating_dependencies(self, asset_id: str) -> list[dict]:
        return self._get(f"/assets/{asset_id}/floating-dependencies").json()

    # --- pipeline graph + lifecycle + bulk (Phase 11) ---
    def dependents(self, asset_id: str, rel_types: list[str] | None = None,
                   depth: int | None = None) -> list[dict]:
        """Transitive impact: what (transitively) depends on this asset."""
        params: dict[str, Any] = {}
        if rel_types:
            params["rel_types"] = ",".join(rel_types)
        if depth is not None:
            params["depth"] = depth
        return self._get(f"/assets/{asset_id}/dependents", params).json()

    def dependencies(self, asset_id: str, rel_types: list[str] | None = None,
                     depth: int | None = None) -> list[dict]:
        """Transitive: what this asset is built from / depends on."""
        params: dict[str, Any] = {}
        if rel_types:
            params["rel_types"] = ",".join(rel_types)
        if depth is not None:
            params["depth"] = depth
        return self._get(f"/assets/{asset_id}/dependencies", params).json()

    def stale_derivations(self, asset_id: str) -> list[dict]:
        return self._get(f"/assets/{asset_id}/stale-derivations").json()

    def relocate(self, asset_id: str, new_location_uri: str, actor: str,
                 facet: str = "source", new_revision: str | None = None) -> None:
        self._post(f"/assets/{asset_id}/relocate", {
            "new_location_uri": new_location_uri, "actor": actor,
            "facet": facet, "new_revision": new_revision,
        })

    def deprecate(self, asset_id: str, actor: str) -> None:
        self._post(f"/assets/{asset_id}/deprecate", {"actor": actor})

    def bulk_declare(self, specs: list[dict]) -> list[str]:
        """specs: [{asset_type, created_by, origin?}, ...] -> minted ids."""
        return self._post("/bulk/declare", {"specs": specs}).json()["ids"]

    def bulk_relate(self, edges: list[dict]) -> int:
        """edges: [{from_asset, to_asset, rel_type, actor?, binding_mode?, pinned_version?}, ...]."""
        return self._post("/bulk/relate", {"edges": edges}).json()["count"]

    def bulk_relocate(self, moves: list[dict]) -> int:
        """moves: [{asset_id, new_location_uri, actor, facet?, new_revision?}, ...]."""
        return self._post("/bulk/relocate", {"moves": moves}).json()["count"]
