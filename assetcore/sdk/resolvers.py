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
from typing import Protocol, runtime_checkable


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
