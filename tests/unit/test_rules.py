"""Pure-function tests for core/rules.py — no repo, no I/O, microseconds.

These pin the *meaning* of the system: version monotonicity, the one-latest
invariant, the float/pin resolution brain, edge validity, and the never-strip-
identity guard. If any of these break, the whole architecture's guarantees break.
"""
from uuid import uuid4

import pytest

from assetcore.core import rules
from assetcore.core.entities import Asset, IdentityFacet, Relationship, SourceVersion
from assetcore.core.types import BindingMode, RelType


def _src(version_num: int, is_latest: bool = False) -> SourceVersion:
    return SourceVersion(
        asset_id=uuid4(),
        location_uri=f"//depot/v{version_num}.ma",
        tool="maya",
        revision=str(version_num),
        version_num=version_num,
        is_latest=is_latest,
    )


# --- next_version_num -------------------------------------------------------
def test_next_version_num_empty_is_one():
    assert rules.next_version_num([]) == 1


def test_next_version_num_is_max_plus_one():
    existing = [_src(1), _src(2), _src(3)]
    assert rules.next_version_num(existing) == 4


def test_next_version_num_uses_max_not_count():
    # gaps must not matter — it's max+1, not len+1
    existing = [_src(2), _src(7)]
    assert rules.next_version_num(existing) == 8


# --- demote_latest ----------------------------------------------------------
def test_demote_latest_clears_the_single_latest():
    existing = [_src(1), _src(2), _src(3, is_latest=True)]
    rules.demote_latest(existing)
    assert sum(1 for v in existing if v.is_latest) == 0


def test_demote_latest_noop_when_none_latest():
    existing = [_src(1), _src(2)]
    rules.demote_latest(existing)
    assert all(not v.is_latest for v in existing)


# --- resolve_dependency_version (the float/pin brain) -----------------------
def test_float_returns_the_latest():
    v1, v2 = _src(1), _src(2, is_latest=True)
    edge = Relationship(uuid4(), uuid4(), RelType.DEPENDS_ON, binding_mode=BindingMode.FLOAT)
    assert rules.resolve_dependency_version(edge, [v1, v2]) is v2


def test_pin_returns_the_pinned_version():
    v1, v2, v3 = _src(1), _src(2), _src(3, is_latest=True)
    edge = Relationship(uuid4(), uuid4(), RelType.DEPENDS_ON,
                        binding_mode=BindingMode.PIN, pinned_version=2)
    assert rules.resolve_dependency_version(edge, [v1, v2, v3]) is v2


def test_pin_to_missing_version_returns_none():
    edge = Relationship(uuid4(), uuid4(), RelType.DEPENDS_ON,
                        binding_mode=BindingMode.PIN, pinned_version=99)
    assert rules.resolve_dependency_version(edge, [_src(1, is_latest=True)]) is None


def test_float_with_no_versions_returns_none():
    edge = Relationship(uuid4(), uuid4(), RelType.DEPENDS_ON, binding_mode=BindingMode.FLOAT)
    assert rules.resolve_dependency_version(edge, []) is None


# --- validate_relationship --------------------------------------------------
def test_binding_mode_on_non_depends_on_raises():
    r = Relationship(uuid4(), uuid4(), RelType.COMPOSED_OF, binding_mode=BindingMode.FLOAT)
    with pytest.raises(ValueError):
        rules.validate_relationship(r)


def test_self_edge_raises():
    a = uuid4()
    r = Relationship(a, a, RelType.DERIVED_FROM)
    with pytest.raises(ValueError):
        rules.validate_relationship(r)


def test_valid_depends_on_with_binding_mode_ok():
    r = Relationship(uuid4(), uuid4(), RelType.DEPENDS_ON, binding_mode=BindingMode.FLOAT)
    rules.validate_relationship(r)   # must not raise


def test_valid_plain_edge_ok():
    r = Relationship(uuid4(), uuid4(), RelType.COMPOSED_OF)
    rules.validate_relationship(r)   # must not raise


# --- can_overwrite_stamp ----------------------------------------------------
def test_overwrite_when_no_existing_stamp():
    assert rules.can_overwrite_stamp(None, "x") is True


def test_overwrite_when_same_stamp():
    assert rules.can_overwrite_stamp("x", "x") is True


def test_no_overwrite_when_different_stamp():
    assert rules.can_overwrite_stamp("x", "y") is False


# --- floating_dependencies (the footgun guard) ------------------------------
def test_floating_dependencies_flags_float_and_unset_not_pin():
    a, m, u, t, p = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    edges = [
        Relationship(a, m, RelType.DEPENDS_ON, binding_mode=BindingMode.FLOAT),
        Relationship(a, u, RelType.DEPENDS_ON),                          # unset == floating
        Relationship(a, t, RelType.DEPENDS_ON, binding_mode=BindingMode.PIN, pinned_version=1),
        Relationship(a, p, RelType.COMPOSED_OF),
    ]
    floating = {e.to_asset for e in rules.floating_dependencies(edges)}
    assert floating == {m, u}      # float + unset flagged; pin + non-DEPENDS_ON not


# --- similarity_score (the dedupe nudge) ------------------------------------
def _asset_with(name, taxonomy="", asset_type="prop", tags=None, origin=None):
    a = Asset(asset_type=asset_type, created_by="amy", origin=origin or {})
    ident = IdentityFacet(asset_id=a.id, display_name=name, taxonomy=taxonomy, tags=tags or [])
    return a, ident


def test_similarity_scores_name_token_overlap():
    a, ident = _asset_with("Weathered Barrel", "props/containers/barrel")
    assert rules.similarity_score("barrel", a, ident) >= 1


def test_similarity_zero_for_unrelated():
    a, ident = _asset_with("Wooden Crate", "props/containers/crate")
    assert rules.similarity_score("barrel", a, ident) == 0


def test_similarity_matches_origin_context():
    a, ident = _asset_with(None, origin={"declared_while_on": "pirate_barrel_ship"})
    assert rules.similarity_score("barrel", a, ident) >= 1
