"""Authority authentication — the L2 concern of *who* is calling.

Pluggable via the provider registry (capability "auth", selected in
assetcore.toml `[auth.main]`), with two implementations:

  * **static** — the original token map: `X-Assetcore-Token` header → authority.
    Dev-grade; the built-in defaults are well-known tokens (see load_tokens).
  * **jwt** — verified identity: an `Authorization: Bearer <JWT>` is validated
    (signature via a shared secret or a JWKS URL, issuer, audience, expiry) and a
    configurable roles claim maps onto the four authorities. IdP-agnostic: any
    OIDC provider that can mint a JWT works. Requires the `auth` extra (PyJWT).

Every authenticated request yields an `AuthContext(authority, subject)`. Under
jwt the `subject` is the token's verified identity and it **overrides** any
caller-supplied `actor`/`published_by` on writes (`resolve_actor`) — provenance
becomes proof, not a self-reported string. Under static, subject is None and the
caller-supplied actor is recorded exactly as before.

The permission model stays the four coarse authorities (production / artist /
engine / build) — they mirror facet sovereignty; per-asset ACLs would fight it.
No business rule lives here.
"""
from __future__ import annotations

import json
import logging
import os
from typing import NamedTuple

from fastapi import Depends, HTTPException, Request

from assetcore.sdk import providers

logger = logging.getLogger(__name__)

# Authorities (kept as plain strings; they're an L2 access concept, not a domain enum)
PRODUCTION = "production"
ARTIST = "artist"
ENGINE = "engine"
BUILD = "build"

AUTHORITIES = (PRODUCTION, ARTIST, ENGINE, BUILD)

DEFAULT_TOKENS: dict[str, str] = {
    "prod-token": PRODUCTION,
    "artist-token": ARTIST,
    "engine-token": ENGINE,
    "build-token": BUILD,
}


class AuthContext(NamedTuple):
    """Who the caller is: their authority, and (when verified) their identity."""

    authority: str
    subject: str | None = None   # set only by verifying providers (jwt)


def resolve_actor(ctx: AuthContext, supplied: str | None) -> str:
    """The actor to record on a write.

    A VERIFIED subject (jwt) always wins — provenance is proof, not a claim. With
    the static provider (subject None) the caller-supplied actor is used as
    before, falling back to the authority when omitted.
    """
    return ctx.subject or supplied or ctx.authority


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


# --- providers ---------------------------------------------------------------
class StaticTokenAuth:
    """The original dev-grade scheme: a shared token names an authority."""

    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def authenticate(self, request: Request) -> AuthContext:
        token = request.headers.get("X-Assetcore-Token")
        if not token or token not in self._tokens:
            raise HTTPException(status_code=401, detail="missing or invalid X-Assetcore-Token")
        return AuthContext(authority=self._tokens[token], subject=None)


class JwtAuth:
    """Verified identity: validate a Bearer JWT, map a roles claim to an authority.

    IdP-agnostic — configure either a shared `secret` (HS*) or a `jwks_url`
    (RS*/ES*, the OIDC-standard path), plus `issuer` and `audience`. `role_map`
    translates the IdP's role/group names into the four authorities; a role that
    IS an authority name maps to itself by default. `subject_claim` (default
    "sub") becomes the verified actor recorded on writes.
    """

    def __init__(self, *, secret: str | None = None, jwks_url: str | None = None,
                 issuer: str | None = None, audience: str | None = None,
                 roles_claim: str = "roles", subject_claim: str = "sub",
                 role_map: dict[str, str] | None = None,
                 algorithms: list[str] | None = None) -> None:
        if not secret and not jwks_url:
            raise ValueError("jwt auth needs either a `secret` or a `jwks_url`")
        import jwt as pyjwt   # the `auth` extra; imported lazily so static-only
        self._pyjwt = pyjwt   # installs never need it
        self._secret = secret
        self._jwks_client = pyjwt.PyJWKClient(jwks_url) if jwks_url else None
        self._issuer = issuer
        self._audience = audience
        self._roles_claim = roles_claim
        self._subject_claim = subject_claim
        self._role_map = role_map or {}
        self._algorithms = algorithms or (["HS256"] if secret else ["RS256", "ES256"])

    def _key_for(self, token: str):
        if self._jwks_client is not None:
            return self._jwks_client.get_signing_key_from_jwt(token).key
        return self._secret

    def authenticate(self, request: Request) -> AuthContext:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing Bearer token")
        token = header[len("Bearer "):].strip()
        try:
            claims = self._pyjwt.decode(
                token, self._key_for(token), algorithms=self._algorithms,
                issuer=self._issuer, audience=self._audience,
                options={"require": ["exp"],
                         "verify_aud": self._audience is not None,
                         "verify_iss": self._issuer is not None},
            )
        except Exception as exc:   # expired / bad signature / wrong iss/aud / malformed
            raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc
        authority = self._authority_from(claims)
        if authority is None:
            raise HTTPException(
                status_code=403,
                detail=f"token carries no role mapping to an authority "
                       f"(claim {self._roles_claim!r})")
        subject = claims.get(self._subject_claim)
        return AuthContext(authority=authority, subject=str(subject) if subject else None)

    def _authority_from(self, claims: dict) -> str | None:
        roles = claims.get(self._roles_claim, [])
        if isinstance(roles, str):
            roles = [roles]
        for role in roles:
            mapped = self._role_map.get(role, role if role in AUTHORITIES else None)
            if mapped in AUTHORITIES:
                return mapped
        return None


@providers.register("auth", "static")
def _build_static(config):
    # explicit tokens in config beat the env/default map (config is expanded toml)
    tokens = config.get("tokens") or load_tokens()
    return StaticTokenAuth(tokens)


@providers.register("auth", "jwt")
def _build_jwt(config):
    return JwtAuth(
        secret=config.get("secret") or None,
        jwks_url=config.get("jwks_url") or None,
        issuer=config.get("issuer") or None,
        audience=config.get("audience") or None,
        roles_claim=config.get("roles_claim", "roles"),
        subject_claim=config.get("subject_claim", "sub"),
        role_map=config.get("role_map") or {},
    )


# --- FastAPI dependencies ------------------------------------------------------
def get_authority(request: Request) -> AuthContext:
    """Authenticate the request through the configured provider -> AuthContext."""
    return request.app.state.auth.authenticate(request)


def require(*allowed: str):
    """Dependency factory: caller's authority must be one of `allowed`."""
    allowed_set = set(allowed)

    def _dep(ctx: AuthContext = Depends(get_authority)) -> AuthContext:
        if ctx.authority not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=f"authority '{ctx.authority}' not permitted; requires one of {sorted(allowed_set)}",
            )
        return ctx

    return _dep
