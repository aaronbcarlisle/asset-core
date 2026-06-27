"""The verb endpoints — one route per universal verb, plus the SSE event stream.

Mutations are POST and authority-guarded; queries are GET and open. This layer
only translates HTTP <-> service calls and maps domain errors to status codes;
every rule lives below in app/core.

Handlers are `async def` on purpose: FastAPI runs sync handlers in a threadpool,
which would call the (single, shared) SQLite connection and the in-process
BroadcastSink.emit from worker threads — neither is thread-safe. Running on the
event loop keeps all repo + sink access single-threaded. The verb calls are short
and non-blocking enough for this service's scale.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from assetcore.app.services import AssetcoreService
from assetcore.service import auth
from assetcore.service.events import event_source
from assetcore.service.schemas import (
    AssetMetaOut,
    BindRuntimeRequest,
    BindSourceRequest,
    BulkCountResponse,
    BulkDeclareRequest,
    BulkDeclareResponse,
    BulkRelateRequest,
    BulkRelocateRequest,
    ClaimRequest,
    DeclareRequest,
    DeclareResponse,
    DeprecateRequest,
    GraphNodeOut,
    IdentityOut,
    RelateRequest,
    RelationshipOut,
    RelocateRequest,
    RenameRequest,
    ResolveResponse,
    RuntimeOut,
    SetBindingRequest,
    SimilarCandidate,
    SourceOut,
    VersionResponse,
    WorklistItem,
)

router = APIRouter()


def get_service(request: Request) -> AssetcoreService:
    return request.app.state.service


def _require_asset(service: AssetcoreService, asset_id: UUID) -> None:
    if service.repo.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail=f"no asset {asset_id}")


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/metrics")
async def metrics(request: Request, service: AssetcoreService = Depends(get_service)) -> dict:
    """Operational health: lifecycle mix, facet coverage, provisional age, latency.

    async so it reads app.state.latency on the event loop, not a threadpool worker
    racing the latency middleware.
    """
    data = service.metrics(datetime.now(timezone.utc))
    lat = request.app.state.latency
    data["events_emitted"] = getattr(request.app.state.sink, "last_seq", 0)
    data["request_count"] = lat["count"]
    data["avg_latency_ms"] = round(lat["total_ms"] / lat["count"], 2) if lat["count"] else 0.0
    data["max_latency_ms"] = round(lat["max_ms"], 2)
    return data


# --- identity lifecycle -----------------------------------------------------
@router.post("/assets", response_model=DeclareResponse, status_code=201)
async def declare(body: DeclareRequest, service: AssetcoreService = Depends(get_service),
                  _: str = Depends(auth.require(auth.ARTIST, auth.ENGINE))) -> DeclareResponse:
    aid = service.declare(body.asset_type, body.created_by, body.origin)
    return DeclareResponse(id=aid)


@router.get("/assets/{asset_id}", response_model=ResolveResponse)
async def resolve(asset_id: UUID, service: AssetcoreService = Depends(get_service)) -> ResolveResponse:
    r = service.resolve(asset_id)
    if r["meta"] is None:
        raise HTTPException(status_code=404, detail=f"no asset {asset_id}")
    return ResolveResponse(
        id=r["id"],
        meta=AssetMetaOut.model_validate(r["meta"]),
        identity=IdentityOut.model_validate(r["identity"]) if r["identity"] else None,
        source=SourceOut.model_validate(r["source"]) if r["source"] else None,
        runtime=RuntimeOut.model_validate(r["runtime"]) if r["runtime"] else None,
    )


@router.post("/assets/{asset_id}/claim", status_code=204)
async def claim(asset_id: UUID, body: ClaimRequest, service: AssetcoreService = Depends(get_service),
                _: str = Depends(auth.require(auth.PRODUCTION))) -> Response:
    _require_asset(service, asset_id)
    service.claim(asset_id, body.display_name, body.taxonomy, body.actor, **body.attributes)
    return Response(status_code=204)


@router.post("/assets/{asset_id}/rename", status_code=204)
async def rename(asset_id: UUID, body: RenameRequest, service: AssetcoreService = Depends(get_service),
                 _: str = Depends(auth.require(auth.PRODUCTION))) -> Response:
    _require_asset(service, asset_id)
    service.rename(asset_id, body.new_name, body.actor, body.new_taxonomy)
    return Response(status_code=204)


@router.post("/assets/{asset_id}/relocate", status_code=204)
async def relocate(asset_id: UUID, body: RelocateRequest,
                   service: AssetcoreService = Depends(get_service),
                   _: str = Depends(auth.get_authority)) -> Response:
    """Move the BYTES (a p4 move / reorg): same identity + version + edges, new
    location. Any authenticated authority; the actor is recorded."""
    _require_asset(service, asset_id)
    try:
        service.relocate(asset_id, body.new_location_uri, body.actor, body.facet, body.new_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/assets/{asset_id}/deprecate", status_code=204)
async def deprecate(asset_id: UUID, body: DeprecateRequest,
                    service: AssetcoreService = Depends(get_service),
                    _: str = Depends(auth.require(auth.PRODUCTION))) -> Response:
    _require_asset(service, asset_id)
    service.deprecate(asset_id, body.actor)
    return Response(status_code=204)


# --- facet binds ------------------------------------------------------------
@router.post("/assets/{asset_id}/source", response_model=VersionResponse)
async def bind_source(asset_id: UUID, body: BindSourceRequest,
                      service: AssetcoreService = Depends(get_service),
                      _: str = Depends(auth.require(auth.ARTIST))) -> VersionResponse:
    _require_asset(service, asset_id)
    v = service.bind_source(asset_id, body.location_uri, body.tool, body.revision, body.published_by)
    return VersionResponse(version=v)


@router.post("/assets/{asset_id}/runtime", response_model=VersionResponse)
async def bind_runtime(asset_id: UUID, body: BindRuntimeRequest,
                       service: AssetcoreService = Depends(get_service),
                       _: str = Depends(auth.require(auth.ENGINE, auth.BUILD))) -> VersionResponse:
    _require_asset(service, asset_id)
    v = service.bind_runtime(asset_id, body.location_uri, body.build_id)
    return VersionResponse(version=v)


# --- relationships ----------------------------------------------------------
@router.post("/relate", status_code=204)
async def relate(body: RelateRequest, service: AssetcoreService = Depends(get_service),
                 authority: str = Depends(auth.get_authority)) -> Response:
    # the token authority gates access; the recorded actor is the caller-supplied
    # one (falling back to the authority only when omitted).
    actor = body.actor if body.actor is not None else authority
    try:
        service.relate(body.from_asset, body.to_asset, body.rel_type, actor,
                       body.binding_mode, body.pinned_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/set_binding", status_code=204)
async def set_binding(body: SetBindingRequest, service: AssetcoreService = Depends(get_service),
                      _: str = Depends(auth.get_authority)) -> Response:
    try:
        service.set_binding(body.from_asset, body.to_asset, body.binding_mode, body.pinned_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


# --- queries (open) ---------------------------------------------------------
@router.get("/dependency", response_model=SourceOut | None)
async def resolve_dependency(frm: UUID, to: UUID,
                             service: AssetcoreService = Depends(get_service)) -> SourceOut | None:
    sv = service.resolve_dependency(frm, to)
    return SourceOut.model_validate(sv) if sv is not None else None


@router.get("/assets/{asset_id}/used_by", response_model=list[RelationshipOut])
async def used_by(asset_id: UUID, service: AssetcoreService = Depends(get_service)) -> list[RelationshipOut]:
    return [RelationshipOut.model_validate(r) for r in service.used_by(asset_id)]


@router.get("/assets/{asset_id}/lineage", response_model=list[RelationshipOut])
async def lineage(asset_id: UUID, service: AssetcoreService = Depends(get_service)) -> list[RelationshipOut]:
    return [RelationshipOut.model_validate(r) for r in service.lineage(asset_id)]


def _parse_rel_types(rel_types: str | None) -> list[str] | None:
    return [t for t in rel_types.split(",") if t] if rel_types else None


@router.get("/assets/{asset_id}/dependents", response_model=list[GraphNodeOut])
async def dependents(asset_id: UUID, rel_types: str | None = None, depth: int | None = None,
                     service: AssetcoreService = Depends(get_service)) -> list[GraphNodeOut]:
    """Transitive impact: everything that depends on this asset (what breaks if I
    change/rename/retire it). `rel_types` is comma-separated; `depth` bounds the walk."""
    try:
        reached = service.dependents(asset_id, _parse_rel_types(rel_types), depth)
    except ValueError as exc:   # an invalid rel_types value -> 400, not 500
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [GraphNodeOut(asset_id=a, depth=d, rel_type=rt) for a, d, rt in reached]


@router.get("/assets/{asset_id}/dependencies", response_model=list[GraphNodeOut])
async def dependencies(asset_id: UUID, rel_types: str | None = None, depth: int | None = None,
                       service: AssetcoreService = Depends(get_service)) -> list[GraphNodeOut]:
    """Transitive: everything this asset is built from / depends on."""
    try:
        reached = service.dependencies(asset_id, _parse_rel_types(rel_types), depth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [GraphNodeOut(asset_id=a, depth=d, rel_type=rt) for a, d, rt in reached]


@router.get("/assets/{asset_id}/stale-derivations", response_model=list[RelationshipOut])
async def stale_derivations(asset_id: UUID,
                            service: AssetcoreService = Depends(get_service)) -> list[RelationshipOut]:
    """DERIVED_FROM edges whose source advanced past the derive version (re-bake needed)."""
    return [RelationshipOut.model_validate(r) for r in service.stale_derivations(asset_id)]


# --- human surfaces (Phase 7) ----------------------------------------------
@router.get("/similar", response_model=list[SimilarCandidate])
async def find_similar(name: str, asset_type: str | None = None,
                       service: AssetcoreService = Depends(get_service)) -> list[SimilarCandidate]:
    """Reuse-over-rebuild nudge: existing assets like `name` (advisory only)."""
    return [
        SimilarCandidate(
            id=asset.id, asset_type=asset.asset_type, lifecycle=asset.lifecycle,
            display_name=identity.display_name if identity else None,
            taxonomy=identity.taxonomy if identity else None, score=score,
        )
        for asset, identity, score in service.find_similar(name, asset_type)
    ]


@router.get("/worklist/provisional", response_model=list[WorklistItem])
async def backfill_worklist(service: AssetcoreService = Depends(get_service)) -> list[WorklistItem]:
    """The provisional backfill queue Production grooms (oldest first)."""
    return [
        WorklistItem(
            id=asset.id, asset_type=asset.asset_type, created_by=asset.created_by,
            created_at=asset.created_at.isoformat(), origin=asset.origin,
            display_name=identity.display_name if identity else None,
        )
        for asset, identity in service.backfill_worklist()
    ]


@router.get("/assets/{asset_id}/floating-dependencies", response_model=list[RelationshipOut])
async def floating_dependencies(asset_id: UUID,
                                service: AssetcoreService = Depends(get_service)) -> list[RelationshipOut]:
    """The float-footgun guard: DEPENDS_ON edges still floating before delivery."""
    return [RelationshipOut.model_validate(r) for r in service.floating_dependencies(asset_id)]


# --- bulk (the 100s-of-assets reality) --------------------------------------
@router.post("/bulk/declare", response_model=BulkDeclareResponse, status_code=201)
async def bulk_declare(body: BulkDeclareRequest, service: AssetcoreService = Depends(get_service),
                       _: str = Depends(auth.require(auth.ARTIST, auth.ENGINE))) -> BulkDeclareResponse:
    ids = service.bulk_declare([s.model_dump() for s in body.specs])
    return BulkDeclareResponse(ids=ids)


@router.post("/bulk/relate", response_model=BulkCountResponse)
async def bulk_relate(body: BulkRelateRequest, service: AssetcoreService = Depends(get_service),
                      authority: str = Depends(auth.get_authority)) -> BulkCountResponse:
    edges = [{"frm": e.from_asset, "to": e.to_asset, "rel_type": e.rel_type,
              "actor": e.actor if e.actor is not None else authority,
              "binding_mode": e.binding_mode, "pinned_version": e.pinned_version}
             for e in body.edges]
    try:
        n = service.bulk_relate(edges)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkCountResponse(count=n)


@router.post("/bulk/relocate", response_model=BulkCountResponse)
async def bulk_relocate(body: BulkRelocateRequest, service: AssetcoreService = Depends(get_service),
                        _: str = Depends(auth.get_authority)) -> BulkCountResponse:
    try:
        n = service.bulk_relocate([m.model_dump() for m in body.moves])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkCountResponse(count=n)


# --- the event spine --------------------------------------------------------
@router.get("/events")
async def events(request: Request, after_seq: int = 0) -> StreamingResponse:
    # SSE reconnect: the browser/agent resends the last seq it saw as Last-Event-ID,
    # so a dropped connection resumes with no gap and no manual cursor.
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id and not after_seq:
        try:
            after_seq = int(last_event_id)
        except ValueError:
            pass
    sink = request.app.state.sink
    # SSE needs a subscribable sink (BroadcastSink). A plain EventSink (emit-only,
    # e.g. a bare NotifySink without a listener bridge) can't fan out live.
    if not all(hasattr(sink, m) for m in ("subscribe", "unsubscribe", "history")):
        raise HTTPException(status_code=501,
                            detail="configured event sink does not support SSE streaming")
    return StreamingResponse(
        event_source(sink, request, after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
