"""integrations/shotgrid.py — the ShotGrid tracker integration (L4).

A VIEW over the IDENTITY facet, never an authority over paths. It mirrors identity
outward into ShotGrid and applies tracker edits back as claim/rename — it touches
only identity verbs, never bind_source/bind_runtime/relate. That restraint is the
point: Production gets its tracker without it path-driving the pipeline
(ARCHITECTURE Part 4.1; anti-pattern: tracker driving paths).

Firewall (Part 8): imports only the SDK, never core/app/infra/service.
"""
from __future__ import annotations

from typing import Protocol

from assetcore.sdk import providers
from assetcore.sdk.tracker_adapter import TrackerAdapter


class ShotGridSite(Protocol):
    def upsert(self, asset_id: str, fields: dict) -> None: ...
    def get(self, external_id: str) -> dict: ...


class _RealShotGridSite:
    """Wraps shotgun_api3 (imported lazily — only where ShotGrid is configured)."""

    ENTITY = "Asset"
    UUID_FIELD = "sg_assetcore_uuid"

    def __init__(self, base_url: str, script_name: str, api_key: str, project) -> None:
        import shotgun_api3  # noqa: PLC0415 — lazy; absent without the SG client
        self._sg = shotgun_api3.Shotgun(base_url, script_name=script_name, api_key=api_key)
        # ShotGrid Assets are project-scoped, so creates need a target Project.
        # `project` may be an id (int / numeric str) or a Project name.
        self._project = self._resolve_project(project)

    def _resolve_project(self, project) -> dict:
        if isinstance(project, int) or (isinstance(project, str) and project.isdigit()):
            return {"type": "Project", "id": int(project)}
        rec = self._sg.find_one("Project", [["name", "is", project]])
        if rec is None:
            raise ValueError(f"no ShotGrid Project named {project!r}")
        return {"type": "Project", "id": rec["id"]}

    # map identity fields -> ShotGrid columns; only non-None ones are written, so a
    # mirror never clears a column the core didn't set.
    _FIELD_MAP = {"display_name": "code", "taxonomy": "sg_taxonomy", "status": "sg_status_list"}

    def upsert(self, asset_id: str, fields: dict) -> None:
        data = {col: fields[key] for key, col in self._FIELD_MAP.items()
                if fields.get(key) is not None}
        existing = self._sg.find_one(self.ENTITY, [[self.UUID_FIELD, "is", asset_id]])
        if existing:
            self._sg.update(self.ENTITY, existing["id"], data)
        else:
            self._sg.create(self.ENTITY,
                            {**data, self.UUID_FIELD: asset_id, "project": self._project})

    def get(self, external_id: str) -> dict:
        rec = self._sg.find_one(self.ENTITY, [["id", "is", int(external_id)]],
                                ["code", "sg_taxonomy", self.UUID_FIELD])
        if rec is None:
            raise KeyError(f"no ShotGrid {self.ENTITY} with id {external_id}")
        return {"display_name": rec["code"], "taxonomy": rec.get("sg_taxonomy"),
                "asset_id": rec.get(self.UUID_FIELD)}


class ShotGridAdapter(TrackerAdapter):
    def __init__(self, client, site: ShotGridSite) -> None:
        super().__init__(client)
        self._site = site

    def push_identity(self, asset_id: str, fields: dict) -> None:
        self._site.upsert(asset_id, fields)

    def pull_identity(self, external_id: str) -> dict:
        return self._site.get(external_id)


@providers.register("tracker", "shotgrid",
                    requires=["base_url", "script_name", "api_key", "project"])
def _build_shotgrid(config, client):
    site = _RealShotGridSite(
        base_url=config["base_url"],
        script_name=config["script_name"],
        api_key=config["api_key"],
        project=config["project"],
    )
    return ShotGridAdapter(client, site)
