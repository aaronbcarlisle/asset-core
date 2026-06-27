"""Resolver registry contract (ARCHITECTURE Part 6) — no service needed.

The registry is pure routing by URI scheme; LocalFileResolver is the one runnable
resolver in Phase 4 (Perforce/Git/S3/Unreal arrive in Phase 5 against this same
registry).
"""
import pytest

from assetcore.sdk.resolvers import LocalFileResolver, ResolverRegistry


class _StubResolver:
    def __init__(self, tag):
        self.tag = tag

    def fetch(self, location_uri):
        return self.tag


def test_longest_prefix_wins():
    reg = ResolverRegistry()
    reg.register("/", _StubResolver("root"))
    reg.register("/Game/", _StubResolver("unreal"))
    assert reg.for_uri("/Game/Bob/BP").tag == "unreal"
    assert reg.for_uri("/other/path").tag == "root"


def test_unregistered_scheme_raises():
    reg = ResolverRegistry()
    reg.register("git://", _StubResolver("git"))
    with pytest.raises(KeyError):
        reg.for_uri("//depot/art/barrel.ma")


def test_local_file_resolver_round_trips(tmp_path):
    f = tmp_path / "barrel.ma"
    f.write_text("geometry")
    reg = ResolverRegistry()
    reg.register("file://", LocalFileResolver())
    assert reg.fetch(f"file://{f}") == str(f)


def test_local_file_resolver_missing_raises(tmp_path):
    reg = ResolverRegistry()
    reg.register("file://", LocalFileResolver())
    with pytest.raises(FileNotFoundError):
        reg.fetch(f"file://{tmp_path / 'nope.ma'}")
