"""JWT auth end-to-end: config-selected provider, verified actor on writes.

Builds the real app with ASSETCORE_CONFIG pointing at a toml that selects the
jwt provider (HS256 shared secret), then proves: Bearer tokens gate the verbs by
mapped authority, and the recorded actor is the token's verified subject — even
when the caller self-reports someone else.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
import jwt as pyjwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SECRET = "e2e-secret"
ISSUER = "https://idp.test"
AUD = "assetcore"

TOML = """
[repos.main]
provider = "sqlite"
[repos.main.config]
path = ""

[auth.main]
provider = "jwt"
[auth.main.config]
secret = "{secret}"
issuer = "{issuer}"
audience = "{aud}"
[auth.main.config.role_map]
"content-team" = "artist"
"production-team" = "production"
"""


def _token(roles, sub):
    return pyjwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(minutes=5),
         "iss": ISSUER, "aud": AUD, "roles": roles, "sub": sub},
        SECRET, algorithm="HS256")


def _bearer(roles, sub):
    return {"Authorization": f"Bearer {_token(roles, sub)}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "assetcore.toml"
    cfg.write_text(TOML.format(secret=SECRET, issuer=ISSUER, aud=AUD), encoding="utf-8")
    monkeypatch.setenv("ASSETCORE_CONFIG", str(cfg))
    from assetcore.service.app import create_app
    with TestClient(create_app()) as tc:
        yield tc


def test_bearer_gates_and_maps_authority(client):
    # no token -> 401; artist role can declare; artist role cannot claim (production)
    assert client.post("/assets", json={"asset_type": "prop", "created_by": "x"}).status_code == 401
    r = client.post("/assets", json={"asset_type": "prop", "created_by": "jane"},
                    headers=_bearer(["content-team"], "jane@studio.com"))
    assert r.status_code == 201
    aid = r.json()["id"]
    denied = client.post(f"/assets/{aid}/claim",
                         json={"display_name": "X", "taxonomy": "t"},
                         headers=_bearer(["content-team"], "jane@studio.com"))
    assert denied.status_code == 403
    # the old static token means nothing under the jwt provider
    assert client.post("/assets", json={"asset_type": "prop", "created_by": "x"},
                       headers={"X-Assetcore-Token": "artist-token"}).status_code == 401


def test_verified_subject_overrides_self_reported_actor(client):
    artist = _bearer(["content-team"], "jane@studio.com")
    prod = _bearer(["production-team"], "pat@studio.com")

    aid = client.post("/assets", json={"asset_type": "prop", "created_by": "jane"},
                      headers=artist).json()["id"]
    # caller self-reports a DIFFERENT actor: the verified subject must win
    r = client.post(f"/assets/{aid}/source",
                    json={"location_uri": "//d/a.ma", "tool": "maya", "revision": "1",
                          "published_by": "someone-else"}, headers=artist)
    assert r.status_code == 200
    src = client.get(f"/assets/{aid}").json()["source"]
    assert src["location_uri"] == "//d/a.ma"

    # claim without ANY actor field: the subject is recorded
    r = client.post(f"/assets/{aid}/claim",
                    json={"display_name": "Barrel", "taxonomy": "props/barrel",
                          "actor": "impostor"}, headers=prod)
    assert r.status_code == 204
    sink = client.app.state.sink
    claimed = [e for e in sink.events if e.event_type == "identity.claimed"][-1]
    assert claimed.actor == "pat@studio.com"          # proof, not the impostor string
    published = [e for e in sink.events if e.event_type == "source.published"][-1]
    assert published.actor == "jane@studio.com"


def test_actor_optional_under_jwt(client):
    prod = _bearer(["production-team"], "pat@studio.com")
    artist = _bearer(["content-team"], "jane@studio.com")
    aid = client.post("/assets", json={"asset_type": "prop", "created_by": "jane"},
                      headers=artist).json()["id"]
    # no actor supplied anywhere: still 204, subject recorded
    assert client.post(f"/assets/{aid}/claim",
                       json={"display_name": "B", "taxonomy": "t"},
                       headers=prod).status_code == 204
    assert client.post(f"/assets/{aid}/rename",
                       json={"new_name": "B2"}, headers=prod).status_code == 204
    renamed = [e for e in client.app.state.sink.events if e.event_type == "identity.renamed"][-1]
    assert renamed.actor == "pat@studio.com"
