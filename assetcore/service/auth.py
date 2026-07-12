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
import logging
import os

from fastapi import Depends, Header, HTTPException, Request

logger = logging.getLogger(__name__)

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


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def load_tokens() -> dict[str, str]:
    """Token->authority map. ASSETCORE_TOKENS (JSON) overrides the dev defaults.

    The built-in defaults are WELL-KNOWN dev tokens — fine locally, dangerous
    exposed. If ASSETCORE_TOKENS is unset we warn loudly; set
    ASSETCORE_REQUIRE_TOKENS=1 to fail startup instead of falling back to them
    (the production-safe posture).
    """
    raw = os.environ.get("ASSETCORE_TOKENS")
    if raw:
        return json.loads(raw)
    if _truthy(os.environ.get("ASSETCORE_REQUIRE_TOKENS")):
        raise RuntimeError(
            "ASSETCORE_REQUIRE_TOKENS is set but ASSETCORE_TOKENS is not; refusing to "
            "start with the built-in dev tokens. Provide a token->authority JSON map "
            "in ASSETCORE_TOKENS.")
    logger.warning(
        "no ASSETCORE_TOKENS set — using built-in DEV tokens (prod-token, artist-token, "
        "engine-token, build-token). Do NOT expose this service; set ASSETCORE_TOKENS, or "
        "ASSETCORE_REQUIRE_TOKENS=1 to fail closed.")
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
