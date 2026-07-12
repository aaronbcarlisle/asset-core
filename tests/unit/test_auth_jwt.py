"""JwtAuth — verified identity mapped onto the four authorities.

HS256 with a shared secret keeps these tests dependency-light (no IdP); the JWKS
path uses the same PyJWT validation machinery. Every rejection is a 401 except
"valid token, no mappable role" which is a 403 (authenticated but not permitted).
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("jwt")
import jwt as pyjwt  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from assetcore.service.auth import AuthContext, JwtAuth, StaticTokenAuth, resolve_actor  # noqa: E402

SECRET = "test-secret"


class _Req:
    """A minimal request: just the headers the providers read."""

    def __init__(self, **headers):
        self.headers = headers


def _token(secret=SECRET, *, exp_minutes=5, issuer="https://idp.test",
           audience="assetcore", roles=("artists",), sub="jane@studio.com", **extra):
    roles_claim = roles if isinstance(roles, str) else list(roles)   # str stays a str
    claims = {"exp": datetime.now(timezone.utc) + timedelta(minutes=exp_minutes),
              "iss": issuer, "aud": audience, "roles": roles_claim, "sub": sub, **extra}
    return pyjwt.encode(claims, secret, algorithm="HS256")


def _auth(**kw):
    defaults = dict(secret=SECRET, issuer="https://idp.test", audience="assetcore",
                    role_map={"artists": "artist", "pipeline-production": "production"})
    defaults.update(kw)
    return JwtAuth(**defaults)


def test_valid_token_yields_authority_and_subject():
    ctx = _auth().authenticate(_Req(Authorization=f"Bearer {_token()}"))
    assert ctx == AuthContext(authority="artist", subject="jane@studio.com")


def test_role_that_is_an_authority_maps_to_itself():
    tok = _token(roles=["build"])
    ctx = _auth(role_map={}).authenticate(_Req(Authorization=f"Bearer {tok}"))
    assert ctx.authority == "build"


def test_string_roles_claim_accepted():
    tok = _token(roles="pipeline-production")
    ctx = _auth().authenticate(_Req(Authorization=f"Bearer {tok}"))
    assert ctx.authority == "production"


def test_missing_bearer_is_401():
    with pytest.raises(HTTPException) as exc:
        _auth().authenticate(_Req())
    assert exc.value.status_code == 401


def test_expired_token_is_401():
    tok = _token(exp_minutes=-5)
    with pytest.raises(HTTPException) as exc:
        _auth().authenticate(_Req(Authorization=f"Bearer {tok}"))
    assert exc.value.status_code == 401


def test_wrong_issuer_and_wrong_audience_are_401():
    for tok in (_token(issuer="https://evil.test"), _token(audience="other-api")):
        with pytest.raises(HTTPException) as exc:
            _auth().authenticate(_Req(Authorization=f"Bearer {tok}"))
        assert exc.value.status_code == 401


def test_bad_signature_is_401():
    tok = _token(secret="not-the-secret")
    with pytest.raises(HTTPException) as exc:
        _auth().authenticate(_Req(Authorization=f"Bearer {tok}"))
    assert exc.value.status_code == 401


def test_valid_token_without_mappable_role_is_403():
    tok = _token(roles=["janitors"])
    with pytest.raises(HTTPException) as exc:
        _auth().authenticate(_Req(Authorization=f"Bearer {tok}"))
    assert exc.value.status_code == 403


def test_config_requires_secret_or_jwks():
    with pytest.raises(ValueError):
        JwtAuth()


def test_resolve_actor_precedence():
    verified = AuthContext(authority="artist", subject="jane@studio.com")
    anonymous = AuthContext(authority="artist", subject=None)
    assert resolve_actor(verified, "someone-else") == "jane@studio.com"   # proof wins
    assert resolve_actor(anonymous, "amy") == "amy"                       # as before
    assert resolve_actor(anonymous, None) == "artist"                     # fallback


def test_static_provider_still_maps_tokens():
    provider = StaticTokenAuth({"tok": "engine"})
    ctx = provider.authenticate(_Req(**{"X-Assetcore-Token": "tok"}))
    assert ctx == AuthContext(authority="engine", subject=None)
    with pytest.raises(HTTPException):
        provider.authenticate(_Req())
