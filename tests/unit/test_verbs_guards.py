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


def test_claim_on_deprecated_asset_refused_without_reactivate(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "prop", "amy")
    verbs.claim(repo, sink, a, "Barrel", "props/barrel", "pat")
    verbs.deprecate(repo, sink, a, "pat")
    with pytest.raises(ValueError, match="deprecated"):
        verbs.claim(repo, sink, a, "Barrel", "props/barrel", "pat")   # no reactivate
    from assetcore.core.types import Lifecycle
    assert repo.get_asset(a).lifecycle == Lifecycle.DEPRECATED         # stays retired


def test_claim_reactivate_resurrects_deprecated_asset(rs):
    repo, sink = rs
    from assetcore.core.types import Lifecycle
    a = verbs.declare(repo, sink, "prop", "amy")
    verbs.claim(repo, sink, a, "Barrel", "props/barrel", "pat")
    verbs.deprecate(repo, sink, a, "pat")
    verbs.claim(repo, sink, a, "Barrel Redux", "props/barrel", "pat", reactivate=True)
    assert repo.get_asset(a).lifecycle == Lifecycle.ACTIVE
    assert repo.get_identity(a).display_name == "Barrel Redux"
    # the resurrection is auditable on the event
    claimed = [e for e in sink.events if e.event_type == "identity.claimed"]
    assert claimed[-1].payload["reactivated"] is True


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


def test_bind_runtime_records_actor(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "prop", "amy")
    verbs.bind_runtime(repo, sink, a, "/Game/a", "build-1", actor="engine:pat")
    cooked = [e for e in sink.events if e.event_type == "runtime.cooked"][-1]
    assert cooked.actor == "engine:pat"          # not the hardcoded "build"


def test_set_binding_records_actor(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "anim", "lee")
    b = verbs.declare(repo, sink, "material", "mo")
    verbs.relate(repo, sink, a, b, RelType.DEPENDS_ON, "lee", binding_mode=BindingMode.FLOAT)
    verbs.set_binding(repo, sink, a, b, BindingMode.PIN, pinned_version=1, actor="anim:lee")
    changed = [e for e in sink.events if e.event_type == "binding.changed"][-1]
    assert changed.actor == "anim:lee"           # not the hardcoded "consumer"


def test_relate_pin_without_version_raises(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "anim", "lee")
    b = verbs.declare(repo, sink, "material", "mo")
    with pytest.raises(ValueError, match="pinned_version"):
        verbs.relate(repo, sink, a, b, RelType.DEPENDS_ON, "lee",
                     binding_mode=BindingMode.PIN)          # no pinned_version
    assert repo.get_edge(a, b, RelType.DEPENDS_ON) is None  # nothing persisted


def test_set_binding_pin_without_version_raises(rs):
    repo, sink = rs
    a = verbs.declare(repo, sink, "anim", "lee")
    b = verbs.declare(repo, sink, "material", "mo")
    verbs.relate(repo, sink, a, b, RelType.DEPENDS_ON, "lee", binding_mode=BindingMode.FLOAT)
    with pytest.raises(ValueError, match="pinned_version"):
        verbs.set_binding(repo, sink, a, b, BindingMode.PIN)   # no pinned_version
    # the edge stays FLOAT — the bad flip did not take
    assert repo.get_edge(a, b, RelType.DEPENDS_ON).binding_mode == BindingMode.FLOAT


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
