"""sdk/providers.py — capability provider registry (generalizes ResolverRegistry).

A "capability" is a stable interface (tracker, repo, source-vcs, runtime-store). A
"provider" is one named implementation of it (shotgrid, jira, sqlite, postgres).
Providers self-register by name; the active one is chosen by CONFIG, never by an
import in application code. This is the same plugin pattern as `ResolverRegistry`,
lifted to a generic so every swappable service uses one mechanism.

Adding Jira = write JiraAdapter + @register("tracker", "jira"). No caller changes.

Firewall (Part 8): pure stdlib + typing. Imports nothing from core/app/infra/
service — registrations live in the integrations/infra modules that import THIS.
"""
from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")

# capability -> { provider_name -> factory(config: dict, **injected) -> instance }
_REGISTRY: dict[str, dict[str, Callable[..., object]]] = {}


def register(capability: str, name: str):
    """Decorator: register a provider factory under a capability + name."""
    def _wrap(factory: Callable[..., T]) -> Callable[..., T]:
        _REGISTRY.setdefault(capability, {})[name] = factory
        return factory
    return _wrap


def build(capability: str, name: str, config: dict, **injected):
    """Instantiate the configured provider. `injected` carries runtime deps the
    factory needs but config can't hold (e.g. the AssetcoreClient)."""
    try:
        factory = _REGISTRY[capability][name]
    except KeyError:
        avail = available(capability)
        hint = (avail if avail else
                "(none registered — did the composition root import the provider "
                "modules, e.g. assetcore.infra._providers / "
                "assetcore.integrations._register?)")
        raise KeyError(
            f"no provider {name!r} for capability {capability!r}; available: {hint}")
    return factory(config=config, **injected)


def available(capability: str) -> list[str]:
    return sorted(_REGISTRY.get(capability, {}))
