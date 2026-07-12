"""The verb endpoints — one route per universal verb, plus the SSE event stream.

Mutations are POST and authority-guarded; queries are GET and open. This layer
only translates HTTP <-> service calls and maps domain errors to status codes;
every rule lives below in app/core.

Handlers are `async def`, and every service/repo call goes through the `run`
dependency (`get_run`), which decides WHERE the (synchronous) DB work executes:

  * SQLite + BroadcastSink (the zero-setup default) are loop-confined — one shared
    connection, an asyncio queue — so calls run INLINE on the event loop, exactly
    the single-threaded model this service always had.
  * The pooled PostgresRepo + postgres sink are thread-safe
    (`SUPPORTS_CONCURRENCY`), so calls run in the threadpool
    (`run_in_threadpool`) — a slow query no longer blocks the event loop, and
    requests genuinely execute concurrently on separate pooled connections.

create_app sets `app.state.offload_db` from what the configured repo+sink declare.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from assetcore.app.services import AssetcoreService, DeclareConflict
from assetcore.core.errors import VersionConflict
from assetcore.service import auth
from assetcore.service.events import event_source
from assetcore.service.schemas import (
    AssetMetaOut,
    AssetSummaryOut,
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


async def _run_inline(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def get_run(request: Request):
    """The execution seam: inline on the loop (sqlite/broadcast — loop-confined) or
    in the threadpool (pooled postgres — thread-safe, non-blocking). See module doc."""
    return run_in_threadpool if request.app.state.offload_db else _run_inline


def _require_asset(service: AssetcoreService, asset_id: UUID) -> None:
    if service.repo.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail=f"no asset {asset_id}")


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/metrics")
async def metrics(request: Request, service: AssetcoreService = Depends(get_service),
                  run=Depends(get_run)) -> dict:
    """Operational health: lifecycle mix, facet coverage, provisional age, latency.

    async so it reads app.state.latency on the event loop, not a threadpool worker
    racing the latency middleware.
    """
    data = await run(service.metrics, datetime.now(timezone.utc))
    lat = request.app.state.latency
    data["events_emitted"] = getattr(request.app.state.sink, "last_seq", 0)
    data["request_count"] = lat["count"]
    data["avg_latency_ms"] = round(lat["total_ms"] / lat["count"], 2) if lat["count"] else 0.0
    data["max_latency_ms"] = round(lat["max_ms"], 2)
    return data


# --- identity lifecycle -----------------------------------------------------
@router.post("/assets", response_model=DeclareResponse, status_code=201)
async def declare(body: DeclareRequest, response: Response, service: AssetcoreService = Depends(get_service),
                  _: auth.AuthContext = Depends(auth.require(auth.ARTIST, auth.ENGINE)),
                  run=Depends(get_run)) -> DeclareResponse:
    try:
        result = await run(service.declare, body.asset_type, body.created_by, body.origin,
                           asset_id=body.id)
    except DeclareConflict as exc:   # same id, different payload -> genuine collision
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.created:
        response.status_code = 200
    return DeclareResponse(id=result.id)


@router.get("/assets", response_model=list[AssetSummaryOut])
async def list_assets(
    created_by: str | None = None,
    taxonomy_prefix: str | None = None,
    updated_since: datetime | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    service: AssetcoreService = Depends(get_service),
    run=Depends(get_run),
) -> list[AssetSummaryOut]:
    assets = await run(
        service.list_assets,
        created_by=created_by,
        taxonomy_prefix=taxonomy_prefix,
        updated_since=updated_since,
        limit=limit,
        offset=offset,
    )
    return [
        AssetSummaryOut(
            id=a["id"],
            asset_type=a["asset_type"],
            created_by=a["created_by"],
            created_at=a["created_at"],
            meta=AssetMetaOut.model_validate(a["meta"]) if a["meta"] else None,
            identity=IdentityOut.model_validate(a["identity"]) if a["identity"] else None,
            source=SourceOut.model_validate(a["source"]) if a["source"] else None,
        )
        for a in assets
    ]


@router.get("/assets/{asset_id}", response_model=ResolveResponse)
async def resolve(asset_id: UUID, service: AssetcoreService = Depends(get_service),
                  run=Depends(get_run)) -> ResolveResponse:
    r = await run(service.resolve, asset_id)
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
                ctx: auth.AuthContext = Depends(auth.require(auth.PRODUCTION)),
                run=Depends(get_run)) -> Response:
    await run(_require_asset, service, asset_id)
    try:
        await run(service.claim, asset_id, body.display_name, body.taxonomy,
                  auth.resolve_actor(ctx, body.actor),
                  reactivate=body.reactivate, attributes=body.attributes)
    except ValueError as exc:   # claiming a deprecated asset without reactivate=True
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/assets/{asset_id}/rename", status_code=204)
async def rename(asset_id: UUID, body: RenameRequest, service: AssetcoreService = Depends(get_service),
                 ctx: auth.AuthContext = Depends(auth.require(auth.PRODUCTION)),
                 run=Depends(get_run)) -> Response:
    await run(_require_asset, service, asset_id)
    await run(service.rename, asset_id, body.new_name,
              auth.resolve_actor(ctx, body.actor), body.new_taxonomy)
    return Response(status_code=204)


@router.post("/assets/{asset_id}/relocate", status_code=204)
async def relocate(asset_id: UUID, body: RelocateRequest,
                   service: AssetcoreService = Depends(get_service),
                   ctx: auth.AuthContext = Depends(auth.get_authority),
                   run=Depends(get_run)) -> Response:
    """Move the BYTES (a p4 move / reorg): same identity + version + edges, new
    location. Any authenticated authority; the actor is recorded."""
    await run(_require_asset, service, asset_id)
    try:
        await run(service.relocate, asset_id, body.new_location_uri,
                  auth.resolve_actor(ctx, body.actor), body.facet, body.new_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/assets/{asset_id}/deprecate", status_code=204)
async def deprecate(asset_id: UUID, body: DeprecateRequest,
                    service: AssetcoreService = Depends(get_service),
                    ctx: auth.AuthContext = Depends(auth.require(auth.PRODUCTION)),
                    run=Depends(get_run)) -> Response:
    await run(_require_asset, service, asset_id)
    await run(service.deprecate, asset_id, auth.resolve_actor(ctx, body.actor))
    return Response(status_code=204)


# --- facet binds ------------------------------------------------------------
@router.post("/assets/{asset_id}/source", response_model=VersionResponse)
async def bind_source(asset_id: UUID, body: BindSourceRequest,
                      service: AssetcoreService = Depends(get_service),
                      ctx: auth.AuthContext = Depends(auth.require(auth.ARTIST)),
                      run=Depends(get_run)) -> VersionResponse:
    await run(_require_asset, service, asset_id)
    try:
        v = await run(service.bind_source, asset_id, body.location_uri, body.tool,
                      body.revision, auth.resolve_actor(ctx, body.published_by))
    except VersionConflict as exc:   # lost the version race past the retry budget -> retryable
        raise HTTPException(status_code=503, detail=str(exc),
                            headers={"Retry-After": "1"}) from exc
    return VersionResponse(version=v)


@router.get("/assets/{asset_id}/source/versions", response_model=list[SourceOut])
async def source_versions(asset_id: UUID,
                          service: AssetcoreService = Depends(get_service),
                          run=Depends(get_run)) -> list[SourceOut]:
    """Full source version history (ascending), newest reachable via is_latest."""
    await run(_require_asset, service, asset_id)
    return [SourceOut.model_validate(v) for v in await run(service.source_versions, asset_id)]


@router.get("/assets/{asset_id}/runtime/versions", response_model=list[RuntimeOut])
async def runtime_versions(asset_id: UUID,
                           service: AssetcoreService = Depends(get_service),
                           run=Depends(get_run)) -> list[RuntimeOut]:
    """Full runtime version history (ascending)."""
    await run(_require_asset, service, asset_id)
    return [RuntimeOut.model_validate(v) for v in await run(service.runtime_versions, asset_id)]


@router.post("/assets/{asset_id}/runtime", response_model=VersionResponse)
async def bind_runtime(asset_id: UUID, body: BindRuntimeRequest,
                       service: AssetcoreService = Depends(get_service),
                       ctx: auth.AuthContext = Depends(auth.require(auth.ENGINE, auth.BUILD)),
                       run=Depends(get_run)) -> VersionResponse:
    await run(_require_asset, service, asset_id)
    actor = auth.resolve_actor(ctx, body.actor)
    try:
        v = await run(service.bind_runtime, asset_id, body.location_uri, body.build_id, actor)
    except VersionConflict as exc:   # lost the version race past the retry budget -> retryable
        raise HTTPException(status_code=503, detail=str(exc),
                            headers={"Retry-After": "1"}) from exc
    return VersionResponse(version=v)


# --- relationships ----------------------------------------------------------
@router.post("/relate", status_code=204)
async def relate(body: RelateRequest, service: AssetcoreService = Depends(get_service),
                 ctx: auth.AuthContext = Depends(auth.get_authority),
                 run=Depends(get_run)) -> Response:
    # a verified subject (jwt) is recorded as the actor; otherwise the
    # caller-supplied one, falling back to the authority when omitted.
    actor = auth.resolve_actor(ctx, body.actor)
    try:
        await run(service.relate, body.from_asset, body.to_asset, body.rel_type, actor,
                  body.binding_mode, body.pinned_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/set_binding", status_code=204)
async def set_binding(body: SetBindingRequest, service: AssetcoreService = Depends(get_service),
                      ctx: auth.AuthContext = Depends(auth.get_authority),
                      run=Depends(get_run)) -> Response:
    actor = auth.resolve_actor(ctx, body.actor)
    try:
        await run(service.set_binding, body.from_asset, body.to_asset, body.binding_mode,
                  body.pinned_version, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


# --- queries (open) ---------------------------------------------------------
@router.get("/dependency", response_model=SourceOut | None)
async def resolve_dependency(frm: UUID, to: UUID,
                             service: AssetcoreService = Depends(get_service),
                             run=Depends(get_run)) -> SourceOut | None:
    sv = await run(service.resolve_dependency, frm, to)
    return SourceOut.model_validate(sv) if sv is not None else None


@router.get("/assets/{asset_id}/used_by", response_model=list[RelationshipOut])
async def used_by(asset_id: UUID, service: AssetcoreService = Depends(get_service),
                  run=Depends(get_run)) -> list[RelationshipOut]:
    return [RelationshipOut.model_validate(r) for r in await run(service.used_by, asset_id)]


@router.get("/assets/{asset_id}/lineage", response_model=list[RelationshipOut])
async def lineage(asset_id: UUID, service: AssetcoreService = Depends(get_service),
                  run=Depends(get_run)) -> list[RelationshipOut]:
    return [RelationshipOut.model_validate(r) for r in await run(service.lineage, asset_id)]


def _parse_rel_types(rel_types: str | None) -> list[str] | None:
    return [t for t in rel_types.split(",") if t] if rel_types else None


@router.get("/assets/{asset_id}/dependents", response_model=list[GraphNodeOut])
async def dependents(asset_id: UUID, rel_types: str | None = None, depth: int | None = None,
                     service: AssetcoreService = Depends(get_service),
                     run=Depends(get_run)) -> list[GraphNodeOut]:
    """Transitive impact: everything that depends on this asset (what breaks if I
    change/rename/retire it). `rel_types` is comma-separated; `depth` bounds the walk."""
    try:
        reached = await run(service.dependents, asset_id, _parse_rel_types(rel_types), depth)
    except ValueError as exc:   # an invalid rel_types value -> 400, not 500
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [GraphNodeOut(asset_id=a, depth=d, rel_type=rt) for a, d, rt in reached]


@router.get("/assets/{asset_id}/dependencies", response_model=list[GraphNodeOut])
async def dependencies(asset_id: UUID, rel_types: str | None = None, depth: int | None = None,
                       service: AssetcoreService = Depends(get_service),
                       run=Depends(get_run)) -> list[GraphNodeOut]:
    """Transitive: everything this asset is built from / depends on."""
    try:
        reached = await run(service.dependencies, asset_id, _parse_rel_types(rel_types), depth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [GraphNodeOut(asset_id=a, depth=d, rel_type=rt) for a, d, rt in reached]


@router.get("/assets/{asset_id}/stale-derivations", response_model=list[RelationshipOut])
async def stale_derivations(asset_id: UUID,
                            service: AssetcoreService = Depends(get_service),
                            run=Depends(get_run)) -> list[RelationshipOut]:
    """DERIVED_FROM edges whose source advanced past the derive version (re-bake needed)."""
    return [RelationshipOut.model_validate(r)
            for r in await run(service.stale_derivations, asset_id)]


# --- human surfaces (Phase 7) ----------------------------------------------
@router.get("/similar", response_model=list[SimilarCandidate])
async def find_similar(name: str, asset_type: str | None = None,
                       service: AssetcoreService = Depends(get_service),
                       run=Depends(get_run)) -> list[SimilarCandidate]:
    """Reuse-over-rebuild nudge: existing assets like `name` (advisory only)."""
    return [
        SimilarCandidate(
            id=asset.id, asset_type=asset.asset_type, lifecycle=asset.lifecycle,
            display_name=identity.display_name if identity else None,
            taxonomy=identity.taxonomy if identity else None, score=score,
        )
        for asset, identity, score in await run(service.find_similar, name, asset_type)
    ]


@router.get("/worklist/provisional", response_model=list[WorklistItem])
async def backfill_worklist(
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    service: AssetcoreService = Depends(get_service),
    run=Depends(get_run),
) -> list[WorklistItem]:
    """The provisional backfill queue Production grooms (oldest first)."""
    return [
        WorklistItem(
            id=asset.id, asset_type=asset.asset_type, created_by=asset.created_by,
            created_at=asset.created_at, origin=asset.origin,
            display_name=identity.display_name if identity else None,
        )
        for asset, identity in await run(service.backfill_worklist, limit=limit, offset=offset)
    ]


@router.get("/assets/{asset_id}/floating-dependencies", response_model=list[RelationshipOut])
async def floating_dependencies(asset_id: UUID,
                                service: AssetcoreService = Depends(get_service),
                                run=Depends(get_run)) -> list[RelationshipOut]:
    """The float-footgun guard: DEPENDS_ON edges still floating before delivery."""
    return [RelationshipOut.model_validate(r)
            for r in await run(service.floating_dependencies, asset_id)]


# --- bulk (the 100s-of-assets reality) --------------------------------------
@router.post("/bulk/declare", response_model=BulkDeclareResponse, status_code=201)
async def bulk_declare(body: BulkDeclareRequest, service: AssetcoreService = Depends(get_service),
                       _: auth.AuthContext = Depends(auth.require(auth.ARTIST, auth.ENGINE)),
                       run=Depends(get_run)) -> BulkDeclareResponse:
    ids = await run(service.bulk_declare, [s.model_dump() for s in body.specs])
    return BulkDeclareResponse(ids=ids)


@router.post("/bulk/relate", response_model=BulkCountResponse)
async def bulk_relate(body: BulkRelateRequest, service: AssetcoreService = Depends(get_service),
                      ctx: auth.AuthContext = Depends(auth.get_authority),
                      run=Depends(get_run)) -> BulkCountResponse:
    edges = [{"frm": e.from_asset, "to": e.to_asset, "rel_type": e.rel_type,
              "actor": auth.resolve_actor(ctx, e.actor),
              "binding_mode": e.binding_mode, "pinned_version": e.pinned_version}
             for e in body.edges]
    try:
        n = await run(service.bulk_relate, edges)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkCountResponse(count=n)


@router.post("/bulk/relocate", response_model=BulkCountResponse)
async def bulk_relocate(body: BulkRelocateRequest, service: AssetcoreService = Depends(get_service),
                        ctx: auth.AuthContext = Depends(auth.get_authority),
                        run=Depends(get_run)) -> BulkCountResponse:
    moves = [{**m.model_dump(), "actor": auth.resolve_actor(ctx, m.actor)}
             for m in body.moves]
    try:
        n = await run(service.bulk_relocate, moves)
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
