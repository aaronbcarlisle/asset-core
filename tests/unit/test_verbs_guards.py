"""Guards added from PR review: unknown-asset errors, attribute reset, edge flips."""
from uuid import uuid4

import pytest

from assetcore.app import verbs
from assetcore.core.types import BindingMode, RelType
from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink


@pytest.fixture
def rs():
    return InMemoryRepo(), InMemorySink()


def test_claim_unknown_asset_raises(rs):
    repo, sink = rs
    with pytest.raises(ValueError):
        verbs.claim(repo, sink, uuid4(), "X", "t", "pat")


def test_rename_unknown_asset_raises(rs):
    repo, sink = rs
    with pytest.raises(ValueError):
        verbs.rename(repo, sink, uuid4(), "X", "pat")


def test_claim_with_no_attrs_resets_attributes(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "prop", "amy")
    verbs.claim(repo, sink, a, "Barrel", "t", "pat", note="first")
    assert repo.get_identity(a).attributes == {"note": "first"}
    verbs.claim(repo, sink, a, "Barrel", "t", "pat")          # no attrs -> cleared
    assert repo.get_identity(a).attributes == {}


def test_set_binding_requires_existing_edge(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "anim", "lee")
    b = verbs.declare(repo, sink, "material", "mo")
    with pytest.raises(ValueError):
        verbs.set_binding(repo, sink, a, b, BindingMode.PIN, pinned_version=1)


def test_set_binding_preserves_edge_attributes(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "anim", "lee")
    b = verbs.declare(repo, sink, "material", "mo")
    verbs.relate(repo, sink, a, b, RelType.DEPENDS_ON, "lee", binding_mode=BindingMode.FLOAT)
    repo.get_edge(a, b, RelType.DEPENDS_ON).attributes["note"] = "keep me"
    verbs.set_binding(repo, sink, a, b, BindingMode.PIN, pinned_version=2)
    assert repo.get_edge(a, b, RelType.DEPENDS_ON).attributes == {"note": "keep me"}


def test_relate_event_payload_binding_mode_is_serializable(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "anim", "lee")
    b = verbs.declare(repo, sink, "material", "mo")
    verbs.relate(repo, sink, a, b, RelType.DEPENDS_ON, "lee", binding_mode=BindingMode.FLOAT)
    payload = sink.events[-1].payload
    assert payload["binding_mode"] == "float"     # plain string, not the enum
    assert isinstance(payload["binding_mode"], str)
