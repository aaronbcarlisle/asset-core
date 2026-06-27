"""Request/response models for the HTTP boundary (pydantic v2).

These are the wire contract, kept separate from core entities: the core stays
framework-free, and this is where the Phase-1 "resolve() returns an untyped dict"
flag (#3) is closed — ResolveResponse is the typed shape of the three facets.
"""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from assetcore.core.types import BindingMode, Lifecycle, RelType


# --- requests ---------------------------------------------------------------
class DeclareRequest(BaseModel):
    asset_type: str
    created_by: str
    origin: dict = Field(default_factory=dict)


class ClaimRequest(BaseModel):
    display_name: str
    taxonomy: str
    actor: str
    attributes: dict = Field(default_factory=dict)


class RenameRequest(BaseModel):
    new_name: str
    actor: str
    new_taxonomy: str | None = None


class BindSourceRequest(BaseModel):
    location_uri: str
    tool: str
    revision: str
    published_by: str


class BindRuntimeRequest(BaseModel):
    location_uri: str
    build_id: str


class RelateRequest(BaseModel):
    from_asset: UUID
    to_asset: UUID
    rel_type: RelType
    actor: str
    binding_mode: BindingMode | None = None
    pinned_version: int | None = None


class SetBindingRequest(BaseModel):
    from_asset: UUID
    to_asset: UUID
    binding_mode: BindingMode
    pinned_version: int | None = None


# --- responses --------------------------------------------------------------
class DeclareResponse(BaseModel):
    id: UUID


class VersionResponse(BaseModel):
    version: int


class AssetMetaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_type: str
    lifecycle: Lifecycle
    created_by: str


class IdentityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    display_name: str | None
    taxonomy: str | None
    status: str | None
    tags: list[str]
    attributes: dict


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    location_uri: str
    tool: str
    revision: str
    version_num: int
    is_latest: bool


class RuntimeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    location_uri: str
    build_id: str
    version_num: int
    is_latest: bool


class ResolveResponse(BaseModel):
    id: UUID
    meta: AssetMetaOut | None
    identity: IdentityOut | None
    source: SourceOut | None
    runtime: RuntimeOut | None


class RelationshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    from_asset: UUID
    to_asset: UUID
    rel_type: RelType
    binding_mode: BindingMode | None
    pinned_version: int | None
