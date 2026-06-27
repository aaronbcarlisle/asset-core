"""Authority authentication — the L2 concern of *who* is calling.

A caller presents a token (header X-Assetcore-Token); it maps to an authority:
production / artist / engine / build. Each verb enforces the authority from the
Part-3 table (claim/rename -> production, bind_source -> artist, bind_runtime ->
engine|build, relate/set_binding -> any authenticated, reads -> open).

This is genuine enforcement (wrong authority -> 403), but dev-grade: a static
token map, overridable via the ASSETCORE_TOKENS env var (JSON: token->authority).
Real RBAC / signed identities are Phase 8 hardening. No business rule lives here.
"""
import json
import os

from fastapi import Depends, Header, HTTPException, Request

# Authorities (kept as plain strings; they're an L2 access concept, not a domain enum)
PRODUCTION = "production"
ARTIST = "artist"
ENGINE = "engine"
BUILD = "build"

DEFAULT_TOKENS: dict[str, str] = {
    "prod-token": PRODUCTION,
    "artist-token": ARTIST,
    "engine-token": ENGINE,
    "build-token": BUILD,
}


def load_tokens() -> dict[str, str]:
    """Token->authority map. ASSETCORE_TOKENS (JSON) overrides the dev defaults."""
    raw = os.environ.get("ASSETCORE_TOKENS")
    if raw:
        return json.loads(raw)
    return dict(DEFAULT_TOKENS)


def get_authority(
    request: Request,
    x_assetcore_token: str | None = Header(default=None),
) -> str:
    tokens: dict[str, str] = request.app.state.tokens
    if not x_assetcore_token or x_assetcore_token not in tokens:
        raise HTTPException(status_code=401, detail="missing or invalid X-Assetcore-Token")
    return tokens[x_assetcore_token]


def require(*allowed: str):
    """Dependency factory: caller's authority must be one of `allowed`."""
    allowed_set = set(allowed)

    def _dep(authority: str = Depends(get_authority)) -> str:
        if authority not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=f"authority '{authority}' not permitted; requires one of {sorted(allowed_set)}",
            )
        return authority

    return _dep
