import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import auth
from app.config import Settings

ISSUER = "http://localhost:8080/realms/asesoria-realm"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})()


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(auth, "_jwks_client", lambda url: FakeJWKS())
    return Settings(_env_file=None, auth_mode="keycloak", keycloak_allowed_issuers=ISSUER)


def token(**overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER, "sub": "user-1", "azp": "pwa-client", "iat": now, "exp": now + 300,
        "preferred_username": "cliente.ana", "realm_access": {"roles": ["cliente"]},
    } | overrides
    return jwt.encode(claims, KEY, algorithm="RS256")


def test_valid_token_maps_roles(settings):
    user = auth.decode_token(token(), settings)
    assert user.username == "cliente.ana" and user.has("cliente") and not user.has("gestor")


@pytest.mark.parametrize("overrides", [{"iss": "https://evil.example/realms/asesoria-realm"}, {"azp": "otro"}, {"exp": int(time.time()) - 60}])
def test_rejected_tokens(settings, overrides):
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(token(**overrides), settings)
    assert exc.value.status_code == 401


def test_tampered_signature(settings):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"iss": ISSUER, "sub": "x", "azp": "pwa-client", "iat": 1, "exp": int(time.time()) + 60}, other, algorithm="RS256")
    with pytest.raises(HTTPException):
        auth.decode_token(forged, settings)
