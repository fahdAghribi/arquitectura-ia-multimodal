"""Validación de JWT emitidos por Keycloak (OIDC) y control de acceso por roles (RBAC)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


@dataclass
class User:
    sub: str
    username: str
    roles: set[str] = field(default_factory=set)
    token: str | None = None

    def has(self, role: str) -> bool:
        return role in self.roles

    def public(self) -> dict:
        return {"sub": self.sub, "username": self.username, "roles": sorted(self.roles)}


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=300)


def decode_token(token: str, settings: Settings) -> User:
    if settings.auth_mode == "disabled":
        return User(sub="dev-user", username="dev", roles={settings.role_gestor})
    try:
        signing_key = _jwks_client(settings.jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256", "PS256"],
            issuer=[i.strip() for i in settings.keycloak_allowed_issuers.split(",") if i.strip()],
            options={"verify_aud": False, "require": ["exp", "iat", "iss", "sub"]},
            leeway=10,
        )
    except jwt.PyJWKClientError as exc:
        log.warning("JWKS no disponible: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Proveedor de identidad no disponible") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Token inválido: {exc}") from exc

    allowed_clients = {c.strip() for c in settings.keycloak_allowed_clients.split(",")}
    if claims.get("azp") not in allowed_clients:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token emitido para un cliente no autorizado")

    roles = set(claims.get("realm_access", {}).get("roles", []))
    return User(sub=claims["sub"], username=claims.get("preferred_username", claims["sub"]), roles=roles, token=token)


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> User:
    if settings.auth_mode == "disabled":
        return decode_token("", settings)
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el token Bearer", headers={"WWW-Authenticate": "Bearer"})
    return decode_token(credentials.credentials, settings)


def require_role(*role_attrs: str):
    """Dependencia FastAPI: exige al menos uno de los roles configurados en Settings.<role_attr>."""

    async def checker(user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> User:
        roles = [getattr(settings, a) for a in role_attrs]
        if not any(user.has(r) for r in roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Se requiere uno de los roles: {', '.join(roles)}")
        return user

    return checker


async def websocket_user(websocket: WebSocket, settings: Settings) -> User:
    """Los navegadores no pueden enviar cabeceras en WebSocket: el token viaja como subprotocolo o query."""
    token = websocket.query_params.get("token")
    for proto in websocket.headers.get("sec-websocket-protocol", "").split(","):
        proto = proto.strip()
        if proto.startswith("bearer."):
            token = proto.removeprefix("bearer.")
    if settings.auth_mode != "disabled" and not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el token")
    user = decode_token(token or "", settings)
    if not (user.has(settings.role_cliente) or user.has(settings.role_gestor)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Se requiere el rol 'cliente' o 'gestor'")
    return user
