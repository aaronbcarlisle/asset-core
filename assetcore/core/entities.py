"""Plain data — the entities, mirroring db/schema.sql's five tables.

These are dataclasses with NO logic methods: meaning lives in rules.py, not on
the data. Note the tool-agnostic renames from the prototype (api.py):
  depot_path / engine_path  ->  location_uri   (opaque: //depot/... git:... s3://...)
  dcc                       ->  tool           (just a label)
  p4_changelist (int)       ->  revision (str) (P4 CL, git sha, ... — opaque)
This is the Part-2.1 abstraction: the core stores opaque locations, never parses
them.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .types import BindingMode, Lifecycle, RelType


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> UUID:
    return uuid4()


@dataclass
class Asset:
    """The immutable identity. Born once; only its lifecycle ever changes."""
    asset_type: str
    created_by: str
    id: UUID = field(default_factory=_new_id)
    lifecycle: Lifecycle = Lifecycle.PROVISIONAL
    origin: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)


@dataclass
class IdentityFacet:
    """Owned by Production. A rename touches only this."""
    asset_id: UUID
    display_name: str | None = None
    taxonomy: str | None = None
    status: str | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)


@dataclass
class SourceVersion:
    """Owned by the artist/DCC. A pointer to authored truth, versioned."""
    asset_id: UUID
    location_uri: str          # opaque: //depot/...  git://...  s3://...
    tool: str                  # 'maya','blender','substance' (a label)
    revision: str              # P4 CL, git sha, etc. (str, not int)
    version_num: int
    is_latest: bool = True
    published_by: str | None = None
    published_at: datetime = field(default_factory=_now)


@dataclass
class RuntimeVersion:
    """Owned by the engine/build. Where the cooked asset lives, versioned."""
    asset_id: UUID
    location_uri: str          # '/Game/...' or any engine address scheme
    build_id: str
    version_num: int
    is_latest: bool = True
    cooked_at: datetime = field(default_factory=_now)


@dataclass
class Relationship:
    """A typed, directed edge between two identities."""
    from_asset: UUID
    to_asset: UUID
    rel_type: RelType
    binding_mode: BindingMode | None = None   # DEPENDS_ON only
    pinned_version: int | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class Event:
    """An append-only fact. Every facet write emits one.

    `id` is a stable unique identity for at-least-once delivery: subscribers
    dedupe on it, so a redelivered event (after a reconnect catch-up) is harmless.
    """
    asset_id: UUID | None
    event_type: str
    payload: dict = field(default_factory=dict)
    actor: str | None = None
    occurred_at: datetime = field(default_factory=_now)
    id: UUID = field(default_factory=_new_id)
