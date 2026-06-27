"""sdk/providers.py — capability provider registry (generalizes ResolverRegistry).

A "capability" is a stable interface (tracker, repo, source-vcs, runtime-store). A
"provider" is one named implementation of it (shotgrid, jira, sqlite, postgres).
Providers self-register by name; the active one is chosen by CONFIG, never by an
import in application code. This is the same plugin pattern as `ResolverRegistry`,
lifted to a generic so every swappable service uses one mechanism.

Adding Jira = write JiraAdapter + @register("tracker", "jira"). No caller changes.

A registration also declares the config keys it REQUIRES (`requires=`), so the
config layer can validate a studio's assetcore.toml without knowing any provider's
internals (keeping provider knowledge with the provider — no layering leak).

Firewall (Part 8): pure stdlib + typing. Imports nothing from core/app/infra/
service — registrations live in the integrations/infra modules that import THIS.
"""
from __future__ import annotations

from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

# capability -> { name -> {"factory": fn, "requires": (key, ...)} }
_REGISTRY: dict[str, dict[str, dict]] = {}


def register(capability: str, name: str, *, requires: Iterable[str] = ()):
    """Decorator: register a provider factory under a capability + name.

    `requires` lists the config keys this provider needs — used by config
    validation (Settings.validate), not by build itself.
    """
    req = tuple(requires)

    def _wrap(factory: Callable[..., T]) -> Callable[..., T]:
        _REGISTRY.setdefault(capability, {})[name] = {"factory": factory, "requires": req}
        return factory
    return _wrap


def build(capability: str, name: str, config: dict, **injected):
    """Instantiate the configured provider. `injected` carries runtime deps the
    factory needs but config can't hold (e.g. the AssetcoreClient)."""
    try:
        entry = _REGISTRY[capability][name]
    except KeyError:
        avail = available(capability)
        hint = (avail if avail else
                "(none registered — did the composition root import the provider "
                "modules, e.g. assetcore.infra._providers / "
                "assetcore.integrations._register?)")
        raise KeyError(
            f"no provider {name!r} for capability {capability!r}; available: {hint}"
        ) from None   # the bare KeyError adds nothing; the message above is the error
    return entry["factory"](config=config, **injected)


def available(capability: str) -> list[str]:
    return sorted(_REGISTRY.get(capability, {}))


def capabilities() -> list[str]:
    """Every capability that has at least one provider registered."""
    return sorted(_REGISTRY)


def required_keys(capability: str, name: str) -> tuple[str, ...]:
    """The config keys `name` declared it needs (empty if none / unregistered)."""
    return _REGISTRY.get(capability, {}).get(name, {}).get("requires", ())
