# Configuration-Driven Provider Layer (Phase 9)

Generalizes the existing `ResolverRegistry` into a registry for ANY swappable
backing service (production trackers, the storage repo, and — later — source VCS /
runtime stores). Swapping ShotGrid → Jira, or sqlite → postgres, becomes editing
`assetcore.toml`. No application code change, no conditional in the composition root.

## The principle

ShotGrid, ftrack, and Jira are not three integrations — they are three
*implementations of one capability* ("tracker"). We already had:
- the capability INTERFACE: `sdk/tracker_adapter.py` (`TrackerAdapter`)
- one IMPLEMENTATION: `integrations/shotgrid.py`
- and the exact registry pattern, for bytes: `ResolverRegistry`

This phase adds the missing middle: a generic provider registry + a config layer
that names the live provider. The proof it's the right shape is that `JiraAdapter`
is ~45 lines and nothing below L4 moved.

## Files

- `sdk/providers.py`  — generic capability/provider registry (`register`/`build`/`available`)
- `sdk/settings.py`   — loads `assetcore.toml`, expands `${ENV}`, builds cached providers
- `assetcore.toml`    — the studio's ONLY service-selection surface
- `integrations/jira.py` — `JiraAdapter`, the proof-of-swap (parallels shotgrid.py)
- `integrations/_register.py` — imports every integration so registrations run
- `infra/_providers.py` — registers the storage repos (sqlite/postgres/memory)

## How it slots in

1. Each integration gains a one-line registration factory:

       @providers.register("tracker", "shotgrid")
       def _build_shotgrid(config, client):
           return ShotGridAdapter(client, _RealShotGridSite(**config))

2. `service/app.py`'s default repo now builds through the registry
   (`ASSETCORE_CONFIG` → `Settings.load(...).repo("main")`, else the runnable-here
   sqlite `:memory:` default — still via `providers.build`). The last hard-coded
   backend default is gone; there is no `if/elif` selecting a service.

3. `ResolverRegistry` keeps its own shape. Unifying it into this registry is a
   tidiness pass for later — forcing it now would be abstraction for its own sake.

## Registration ordering (a consequence of the firewall)

`sdk/settings.py` (L3) is firewall-forbidden from importing `infra` (L2) or
`integrations` (L4), so it **cannot** trigger registrations itself. The
**composition root** does it: `service/app.py` imports `assetcore.infra._providers`
before building a repo; a CLI/service that uses trackers imports
`assetcore.integrations._register` at startup. `providers.build` raises a KeyError
that says so if a capability has no providers registered yet.

## Adding Jira (the whole job)

    # integrations/jira.py
    class JiraAdapter(TrackerAdapter):        # same interface as ShotGridAdapter
        def push_identity(self, asset_id, fields): self._site.upsert(asset_id, fields)
        def pull_identity(self, external_id):       return self._site.get(external_id)

    @providers.register("tracker", "jira")
    def _build_jira(config, client):
        return JiraAdapter(client, _RealJiraSite(**config))

Then in `assetcore.toml`: `provider = "jira"`. Done.
`tests/contract/test_providers.py` demonstrates the identical-application-code swap
end to end.

## Why this respects the architecture

- Lives in `sdk/` (L3) + `integrations/` (L4) + a leaf `infra/_providers.py`;
  `core`/`app`/`service` business logic untouched. `lint-imports` stays 3 kept / 0
  broken (the registry is a leaf the layers contract allows infra to import).
- Config holds connection details + a provider NAME only — never identity, never
  paths into the pipeline. A tracker is still a view: it cannot path-drive.
- `${ENV}` expansion keeps secrets out of the file (resolved at load from `os.environ`).
- The registry is the SAME plugin shape as `ResolverRegistry` — one mental model for
  "swap a backing service," whether it's a tracker, a VCS, or a storage scheme.

## Deferred (natural follow-ups)

- Startup config validation: today a misspelled provider name fails at first use
  with an `available()` hint. A "validate the whole `assetcore.toml` on load" pass —
  every named provider exists, every required config key present — is a good
  hardening item once trackers + repos + VCS all flow through the one registry, since
  `assetcore.toml` then becomes the single most operationally important file in a
  deployment.
- Wiring Perforce/git resolvers and runtime stores in as `source_vcs` /
  `runtime_store` providers (the toml already *describes* them).
- Unifying `ResolverRegistry` into the generic registry.
