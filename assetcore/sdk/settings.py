"""sdk/settings.py — load the config, resolve ${ENV} refs, build live providers.

This is the wiring layer: it reads assetcore.toml, expands ${ENV} placeholders
from the environment (so secrets never live in the file), and hands back ready
provider instances by capability + instance name. Application code asks
`settings.tracker("production")` and never knows or cares it's ShotGrid vs Jira.

The toml's section name and the registry's capability name are mapped EXPLICITLY
in `_SECTIONS` — no string-mangling. (An earlier draft derived the capability with
`key.rstrip("s")`, which is a charset strip, not a suffix strip: "source_vcs" ->
"source_vc" -> KeyError. Explicit is correct and obvious.)

`validate()` checks a whole config up front (fail-fast at startup, not at first
use): unknown sections, unknown provider names, missing required keys, and unset
${ENV} references. It can only check provider names for capabilities whose
providers are registered, so the composition root imports the registration modules
before validating (see scripts/validate_config.py and service/app.py).

Firewall (Part 8): imports only the SDK's own `providers`; never core/app/infra/
service. tomllib is stdlib on Python 3.11+ (this project's floor).
"""
from __future__ import annotations

import os
import re
import tomllib  # stdlib (Python 3.11+; see pyproject requires-python)

from assetcore.sdk import providers

_ENV_REF = re.compile(r"\$\{([^}]+)\}")

# toml section -> registry capability (single source of truth for both accessors
# and validation; add a row here when a new capability becomes config-selectable).
_SECTIONS: dict[str, str] = {
    "trackers": "tracker",
    "repos": "repo",
    "source_vcs": "source_vcs",
    "runtime_store": "runtime_store",
}


class ConfigError(Exception):
    """assetcore.toml failed validation. Carries every problem found, not just the
    first, so one run surfaces the whole list."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid assetcore config:\n  - " + "\n  - ".join(problems))


def _expand(value):
    """Recursively replace ${VAR} with os.environ[VAR] (missing -> empty string)."""
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _env_refs(value) -> set[str]:
    """Every ${VAR} name referenced anywhere in a (possibly nested) raw value."""
    if isinstance(value, str):
        return set(_ENV_REF.findall(value))
    if isinstance(value, dict):
        return set().union(*(_env_refs(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_env_refs(v) for v in value)) if value else set()
    return set()


class Settings:
    def __init__(self, config: dict, *, client=None):
        self._config = config
        self._client = client          # injected into tracker providers that need it
        self._cache: dict = {}

    @classmethod
    def load(cls, path: str = "assetcore.toml", *, client=None) -> "Settings":
        with open(path, "rb") as f:
            return cls(tomllib.load(f), client=client)

    def _get(self, section: str, instance: str):
        cache_key = (section, instance)
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            block = self._config[section][instance]
        except KeyError:
            raise KeyError(
                f"no [{section}.{instance}] block in config; "
                f"have {section}: {sorted(self._config.get(section, {}))}")
        capability = _SECTIONS[section]
        cfg = _expand(block.get("config", {}))
        # trackers operate against the running service, so they need the client;
        # storage providers stand alone. Inject only what the capability uses.
        injected = {"client": self._client} if capability == "tracker" else {}
        provider = providers.build(capability, block["provider"], cfg, **injected)
        self._cache[cache_key] = provider
        return provider

    # --- accessors (toml section -> capability via _SECTIONS) ---
    def tracker(self, instance: str = "production"):
        return self._get("trackers", instance)

    def repo(self, instance: str = "main"):
        return self._get("repos", instance)

    def source_vcs(self, instance: str = "main"):
        return self._get("source_vcs", instance)

    def runtime_store(self, instance: str = "main"):
        return self._get("runtime_store", instance)

    # --- validation ---------------------------------------------------------
    def validate(self, capabilities: list[str] | None = None) -> None:
        """Validate the config, raising ConfigError listing every problem found.

        `capabilities` limits which capabilities are checked (the service checks
        just what it consumes, e.g. ["repo"]). Default: every capability that
        currently has providers registered — so describe-only sections for not-yet-
        wired capabilities (no registered providers) don't raise false positives,
        and coverage grows automatically as more providers are wired.
        """
        problems: list[str] = []
        wired = set(providers.capabilities())
        targets = set(capabilities) if capabilities is not None else wired
        section_for = {cap: sec for sec, cap in _SECTIONS.items()}

        # unknown top-level sections (a [trackerss.*] typo) — always worth flagging
        for section in self._config:
            if section not in _SECTIONS:
                problems.append(
                    f"unknown section [{section}] (known: {sorted(_SECTIONS)})")

        for capability in sorted(targets):
            section = section_for.get(capability)
            if section is None or section not in self._config:
                continue
            for instance, block in self._config[section].items():
                where = f"[{section}.{instance}]"
                if not isinstance(block, dict) or "provider" not in block:
                    problems.append(f"{where} is missing a `provider` key")
                    continue
                name = block["provider"]
                avail = providers.available(capability)
                if name not in avail:
                    problems.append(
                        f"{where} provider {name!r} is not registered for "
                        f"{capability!r} (available: {avail or '(none)'})")
                    continue   # can't check required keys for an unknown provider
                cfg = block.get("config", {})
                # Enforce non-emptiness only for REQUIRED keys: an optional key (e.g.
                # sqlite path -> :memory:) may legitimately be empty / an unset env
                # ref, so a blanket env scan would false-positive on the example file.
                for key in providers.required_keys(capability, name):
                    if key not in cfg:
                        problems.append(f"{where} requires config key {key!r}")
                        continue
                    expanded = _expand(cfg[key])
                    if isinstance(expanded, str) and not expanded.strip():
                        refs = sorted(_env_refs(cfg[key]))
                        if refs:
                            problems.append(
                                f"{where} required key {key!r} references "
                                f"${{{', '.join(refs)}}}, unset/empty in the environment")
                        else:
                            problems.append(f"{where} required key {key!r} is empty")

        if problems:
            raise ConfigError(problems)
