"""The verb endpoints — one route per universal verb, plus the SSE event stream.

Mutations are POST and authority-guarded; queries are GET and open. This layer
only translates HTTP <-> service calls and maps domain errors to status codes;
every rule lives below in app/core.
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
    ClaimRequest,
    DeclareRequest,
    DeclareResponse,
    IdentityOut,
    RelateRequest,
    RelationshipOut,
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
def health() -> dict:
    return {"status": "ok"}


@router.get("/metrics")
def metrics(request: Request, service: AssetcoreService = Depends(get_service)) -> dict:
    """Operational health: lifecycle mix, facet coverage, provisional age, latency."""
    data = service.metrics(datetime.now(timezone.utc))
    lat = request.app.state.latency
    data["events_emitted"] = getattr(request.app.state.sink, "last_seq", 0)
    data["request_count"] = lat["count"]
    data["avg_latency_ms"] = round(lat["total_ms"] / lat["count"], 2) if lat["count"] else 0.0
    data["max_latency_ms"] = round(lat["max_ms"], 2)
    return data


# --- identity lifecycle -----------------------------------------------------
@router.post("/assets", response_model=DeclareResponse, status_code=201)
def declare(body: DeclareRequest, service: AssetcoreService = Depends(get_service),
            _: str = Depends(auth.require(auth.ARTIST, auth.ENGINE))) -> DeclareResponse:
    aid = service.declare(body.asset_type, body.created_by, body.origin)
    return DeclareResponse(id=aid)


@router.get("/assets/{asset_id}", response_model=ResolveResponse)
def resolve(asset_id: UUID, service: AssetcoreService = Depends(get_service)) -> ResolveResponse:
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
def claim(asset_id: UUID, body: ClaimRequest, service: AssetcoreService = Depends(get_service),
          _: str = Depends(auth.require(auth.PRODUCTION))) -> Response:
    _require_asset(service, asset_id)
    service.claim(asset_id, body.display_name, body.taxonomy, body.actor, **body.attributes)
    return Response(status_code=204)


@router.post("/assets/{asset_id}/rename", status_code=204)
def rename(asset_id: UUID, body: RenameRequest, service: AssetcoreService = Depends(get_service),
           _: str = Depends(auth.require(auth.PRODUCTION))) -> Response:
    _require_asset(service, asset_id)
    service.rename(asset_id, body.new_name, body.actor, body.new_taxonomy)
    return Response(status_code=204)


# --- facet binds ------------------------------------------------------------
@router.post("/assets/{asset_id}/source", response_model=VersionResponse)
def bind_source(asset_id: UUID, body: BindSourceRequest,
                service: AssetcoreService = Depends(get_service),
                _: str = Depends(auth.require(auth.ARTIST))) -> VersionResponse:
    _require_asset(service, asset_id)
    v = service.bind_source(asset_id, body.location_uri, body.tool, body.revision, body.published_by)
    return VersionResponse(version=v)


@router.post("/assets/{asset_id}/runtime", response_model=VersionResponse)
def bind_runtime(asset_id: UUID, body: BindRuntimeRequest,
                 service: AssetcoreService = Depends(get_service),
                 _: str = Depends(auth.require(auth.ENGINE, auth.BUILD))) -> VersionResponse:
    _require_asset(service, asset_id)
    v = service.bind_runtime(asset_id, body.location_uri, body.build_id)
    return VersionResponse(version=v)


# --- relationships ----------------------------------------------------------
@router.post("/relate", status_code=204)
def relate(body: RelateRequest, service: AssetcoreService = Depends(get_service),
           authority: str = Depends(auth.get_authority)) -> Response:
    try:
        service.relate(body.from_asset, body.to_asset, body.rel_type, body.actor or authority,
                       body.binding_mode, body.pinned_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/set_binding", status_code=204)
def set_binding(body: SetBindingRequest, service: AssetcoreService = Depends(get_service),
                _: str = Depends(auth.get_authority)) -> Response:
    try:
        service.set_binding(body.from_asset, body.to_asset, body.binding_mode, body.pinned_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


# --- queries (open) ---------------------------------------------------------
@router.get("/dependency", response_model=SourceOut | None)
def resolve_dependency(frm: UUID, to: UUID,
                       service: AssetcoreService = Depends(get_service)) -> SourceOut | None:
    sv = service.resolve_dependency(frm, to)
    return SourceOut.model_validate(sv) if sv is not None else None


@router.get("/assets/{asset_id}/used_by", response_model=list[RelationshipOut])
def used_by(asset_id: UUID, service: AssetcoreService = Depends(get_service)) -> list[RelationshipOut]:
    return [RelationshipOut.model_validate(r) for r in service.used_by(asset_id)]


@router.get("/assets/{asset_id}/lineage", response_model=list[RelationshipOut])
def lineage(asset_id: UUID, service: AssetcoreService = Depends(get_service)) -> list[RelationshipOut]:
    return [RelationshipOut.model_validate(r) for r in service.lineage(asset_id)]


# --- human surfaces (Phase 7) ----------------------------------------------
@router.get("/similar", response_model=list[SimilarCandidate])
def find_similar(name: str, asset_type: str | None = None,
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
def backfill_worklist(service: AssetcoreService = Depends(get_service)) -> list[WorklistItem]:
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
def floating_dependencies(asset_id: UUID,
                          service: AssetcoreService = Depends(get_service)) -> list[RelationshipOut]:
    """The float-footgun guard: DEPENDS_ON edges still floating before delivery."""
    return [RelationshipOut.model_validate(r) for r in service.floating_dependencies(asset_id)]


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
    return StreamingResponse(
        event_source(sink, request, after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
