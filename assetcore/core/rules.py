"""The pure business logic — the actual *meaning* of the system.

Every function here is total and side-effect-light: it takes current state in
and returns a decision (or mutates only the plain objects handed to it). NO repo,
NO event sink, NO I/O. This is what test_rules.py exercises directly, in
microseconds, and what tool churn can never reach.

If you are tempted to pass a repo into one of these, the logic belongs in
app/verbs.py instead — that is the boundary this module defends.
"""
from .entities import Relationship, SourceVersion
from .types import BindingMode, RelType


def next_version_num(existing: list) -> int:
    """Version numbers are monotonic per asset per facet: max + 1, starting at 1."""
    return max((v.version_num for v in existing), default=0) + 1


def demote_latest(existing: list) -> None:
    """Invariant: at most one is_latest per asset per facet.

    Mutates the prior latest version(s) to is_latest=False in place. The caller is
    responsible for persisting the change (see app.verbs.bind_source).
    """
    for v in existing:
        if v.is_latest:
            v.is_latest = False


def resolve_dependency_version(
    edge: Relationship,
    dep_versions: list[SourceVersion],
) -> SourceVersion | None:
    """THE float/pin brain — a pure function of an edge and the dependency's versions.

    pin   -> the specific pinned version (or None if it no longer exists).
    float -> the current latest authored version (or None if there are none).
    No database, no tool knowledge — just the decision.
    """
    if edge.binding_mode == BindingMode.PIN:
        return next((v for v in dep_versions if v.version_num == edge.pinned_version), None)
    return next((v for v in dep_versions if v.is_latest), None)


def validate_relationship(r: Relationship) -> None:
    """Relationship validity. Raises ValueError on an invalid edge.

    - binding_mode is meaningful only on DEPENDS_ON edges.
    - no trivial self-referential edge.
    """
    if r.binding_mode is not None and r.rel_type != RelType.DEPENDS_ON:
        raise ValueError("binding_mode is only valid on DEPENDS_ON edges")
    if r.from_asset == r.to_asset:
        raise ValueError("self-referential edge")


def can_overwrite_stamp(existing: str | None, incoming: str) -> bool:
    """Never strip identity: a *different* existing stamp may not be replaced.

    True when there is no existing stamp, or it already matches the incoming one.
    """
    return existing is None or existing == incoming
