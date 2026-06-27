"""Resolution — opaque location_uri -> actual local bytes.

The core stores opaque URIs and never parses them; turning '//depot/...@CL' or
'git://...' into a file on disk is a resolver's job, keyed by URI scheme in a
registry (ARCHITECTURE Part 6). Adding a storage backend is registering a
resolver — the same plugin pattern as tool integration, applied to bytes.

Phase 4 ships the registry, the Resolver protocol, and a runnable LocalFileResolver
(file:// + bare paths). The real PerforceResolver / GitResolver / S3Resolver /
UnrealResolver land in Phase 5; they slot into this same registry untouched.
"""
from __future__ import annotations

import pathlib
import subprocess
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class Resolver(Protocol):
    def fetch(self, location_uri: str) -> str:
        """Materialize the URI locally and return the local path."""


class ResolverRegistry:
    """Maps a URI scheme/prefix to the resolver that knows how to fetch it."""

    def __init__(self) -> None:
        self._by_prefix: dict[str, Resolver] = {}

    def register(self, prefix: str, resolver: Resolver) -> None:
        self._by_prefix[prefix] = resolver

    def for_uri(self, location_uri: str) -> Resolver:
        # longest matching prefix wins (so '/Game/' beats '/')
        for prefix in sorted(self._by_prefix, key=len, reverse=True):
            if location_uri.startswith(prefix):
                return self._by_prefix[prefix]
        raise KeyError(f"no resolver registered for URI: {location_uri!r}")

    def fetch(self, location_uri: str) -> str:
        return self.for_uri(location_uri).fetch(location_uri)


class LocalFileResolver:
    """Resolves 'file://' URIs and bare local paths to an on-disk path."""

    def fetch(self, location_uri: str) -> str:
        path = location_uri[len("file://"):] if location_uri.startswith("file://") else location_uri
        p = pathlib.Path(path)
        if not p.exists():
            raise FileNotFoundError(f"no local file at {p}")
        return str(p)


def _default_p4_runner(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


class PerforceResolver:
    """Resolves '//depot/...[@rev]' URIs by syncing and returning the local path.

    The p4 invocation goes through an injectable runner (default: the `p4` CLI),
    so the command construction is testable without a live Perforce server. Needs
    a configured P4 workspace in production.
    """

    def __init__(self, runner: Callable[[list[str]], str] | None = None) -> None:
        self._run = runner if runner is not None else _default_p4_runner

    def fetch(self, location_uri: str) -> str:
        self._run(["p4", "sync", location_uri])              # pull the revision
        depot = location_uri.split("@", 1)[0]                # strip @CL for `where`
        out = self._run(["p4", "-F", "%path%", "where", depot]).strip()
        if not out:
            raise FileNotFoundError(f"p4 where returned nothing for {depot!r}")
        return out.splitlines()[0]


def default_registry() -> ResolverRegistry:
    """A registry wired with the resolvers runnable today.

    '//' -> Perforce, 'file://' -> local. Git/S3/Unreal resolvers register
    alongside (same pattern) as they're implemented.
    """
    reg = ResolverRegistry()
    reg.register("//", PerforceResolver())
    reg.register("file://", LocalFileResolver())
    return reg
