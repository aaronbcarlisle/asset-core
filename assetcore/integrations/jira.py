"""integrations/jira.py — the Jira tracker integration (L4).

The proof-of-swap: a second TrackerAdapter, parallel to shotgrid.py exactly. That
swapping ShotGrid for Jira is ~45 lines in ONE file — and nothing below L4 moves —
is the whole thesis made concrete (ARCHITECTURE Part 4.1, Part 6 swap test).

A VIEW over the IDENTITY facet: it mirrors identity outward into Jira and applies
tracker edits back as claim/rename. It touches only identity verbs, never
bind_source/bind_runtime/relate — a tracker is a lens, not a path authority.

Firewall (Part 8): imports only the SDK, never core/app/infra/service. The `jira`
client is imported lazily so this module loads cleanly without it installed.
"""
from __future__ import annotations

from typing import Protocol

from assetcore.sdk import providers
from assetcore.sdk.tracker_adapter import TrackerAdapter


class JiraSite(Protocol):
    def upsert(self, asset_id: str, fields: dict) -> None: ...
    def get(self, external_id: str) -> dict: ...


class _RealJiraSite:
    """Wraps the `jira` client (imported lazily — only where Jira is configured)."""

    UUID_FIELD = "customfield_assetcore_uuid"   # a Jira custom field holding identity

    def __init__(self, base_url: str, email: str, api_token: str, project: str) -> None:
        from jira import JIRA  # noqa: PLC0415 — lazy; absent without the jira client
        self._jira = JIRA(server=base_url, basic_auth=(email, api_token))
        self._project = project

    @staticmethod
    def _to_jira_fields(fields: dict) -> dict:
        """Map identity fields -> Jira issue fields with Jira-correct TYPES; only
        non-None ones are written, so a mirror never clears a field the core didn't
        set (mirrors shotgrid's restraint). `labels` is a list of strings, not a
        scalar. `status` is intentionally NOT mapped: in Jira a status is a workflow
        transition, not a writable issue field — writing it would silently fail."""
        data: dict = {}
        if fields.get("display_name") is not None:
            data["summary"] = fields["display_name"]
        if fields.get("taxonomy") is not None:
            data["labels"] = [fields["taxonomy"]]
        return data

    def upsert(self, asset_id: str, fields: dict) -> None:
        data = self._to_jira_fields(fields)
        found = self._jira.search_issues(
            f'project = {self._project} AND "{self.UUID_FIELD}" ~ "{asset_id}"', maxResults=1)
        if found:
            found[0].update(fields=data)
        else:
            self._jira.create_issue(fields={
                "project": {"key": self._project}, "issuetype": {"name": "Task"},
                self.UUID_FIELD: asset_id, **data})

    def get(self, external_id: str) -> dict:
        issue = self._jira.issue(external_id)
        f = issue.fields
        return {"display_name": f.summary,
                "taxonomy": (f.labels[0] if getattr(f, "labels", None) else None),
                "asset_id": getattr(f, self.UUID_FIELD, None)}


class JiraAdapter(TrackerAdapter):
    def __init__(self, client, site: JiraSite) -> None:
        super().__init__(client)
        self._site = site

    def push_identity(self, asset_id: str, fields: dict) -> None:
        self._site.upsert(asset_id, fields)

    def pull_identity(self, external_id: str) -> dict:
        return self._site.get(external_id)


@providers.register("tracker", "jira")
def _build_jira(config, client):
    return JiraAdapter(client, _RealJiraSite(**config))
