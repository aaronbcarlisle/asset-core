"""sdk/settings.py — load the config, resolve ${ENV} refs, build live providers.

This is the wiring layer: it reads assetcore.toml, expands ${ENV} placeholders
from the environment (so secrets never live in the file), and hands back ready
provider instances by capability + instance name. Application code asks
`settings.tracker("production")` and never knows or cares it's ShotGrid vs Jira.

The toml's section name and the registry's capability name are mapped EXPLICITLY
per accessor (see `_ACCESSORS`) — no string-mangling. (An earlier draft derived
the capability with `key.rstrip("s")`, which is a charset strip, not a suffix
strip: "source_vcs" -> "source_vc" -> KeyError. Explicit is correct and obvious.)

Firewall (Part 8): imports only the SDK's own `providers`; never core/app/infra/
service. tomllib is stdlib on Python 3.11+ (this project's floor).
"""
from __future__ import annotations

import os
import re
import tomllib  # stdlib (Python 3.11+; see pyproject requires-python)

from assetcore.sdk import providers

_ENV_REF = re.compile(r"\$\{([^}]+)\}")


def _expand(value):
    """Recursively replace ${VAR} with os.environ[VAR] (missing -> empty string)."""
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class Settings:
    def __init__(self, config: dict, *, client=None):
        self._config = config
        self._client = client          # injected into tracker providers that need it
        self._cache: dict = {}

    @classmethod
    def load(cls, path: str = "assetcore.toml", *, client=None) -> "Settings":
        with open(path, "rb") as f:
            return cls(tomllib.load(f), client=client)

    def _get(self, section: str, capability: str, instance: str):
        cache_key = (section, instance)
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            block = self._config[section][instance]
        except KeyError:
            raise KeyError(
                f"no [{section}.{instance}] block in config; "
                f"have {section}: {sorted(self._config.get(section, {}))}")
        cfg = _expand(block.get("config", {}))
        # trackers operate against the running service, so they need the client;
        # storage providers stand alone. Inject only what the capability uses.
        injected = {"client": self._client} if capability == "tracker" else {}
        provider = providers.build(capability, block["provider"], cfg, **injected)
        self._cache[cache_key] = provider
        return provider

    # --- accessors: (toml section, registry capability) mapped explicitly ---
    def tracker(self, instance: str = "production"):
        return self._get("trackers", "tracker", instance)

    def repo(self, instance: str = "main"):
        return self._get("repos", "repo", instance)

    def source_vcs(self, instance: str = "main"):
        return self._get("source_vcs", "source_vcs", instance)

    def runtime_store(self, instance: str = "main"):
        return self._get("runtime_store", "runtime_store", instance)
