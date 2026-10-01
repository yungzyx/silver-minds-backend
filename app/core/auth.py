"""Verificación de JWT de Supabase Auth.

Dos modos, nunca mezclados para un mismo token:

- JWKS con claves asimétricas (ES256 o RS256). Es el recomendado por Supabase.
- Secreto compartido HS256 (heredado). Se mantiene para desarrollo local.

El algoritmo aceptado lo decide la configuración del servidor, no el token.
"""

import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from app.core.config import Settings, get_settings
from app.core.errors import UnauthenticatedError

ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")
JWKS_CACHE_SECONDS = 600  # Supabase cachea el JWKS 10 minutos; no conviene superarlo
REQUIRED_CLAIMS = ["exp", "sub", "aud"]


@dataclass(frozen=True)
class AuthUser:
    id: uuid.UUID
    email: str | None


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks = (
            PyJWKClient(settings.supabase_jwks_url, cache_keys=True, lifespan=JWKS_CACHE_SECONDS)
            if settings.supabase_jwks_url
            else None
        )

    def verify(self, token: str) -> AuthUser:
        try:
            claims = self._decode(token)
        except jwt.PyJWTError as exc:
            raise UnauthenticatedError("El token no es válido o venció.") from exc

        if claims.get("role") != "authenticated":
            raise UnauthenticatedError("El token no corresponde a una persona usuaria.")
        try:
            user_id = uuid.UUID(str(claims["sub"]))
        except ValueError as exc:
            raise UnauthenticatedError("El token no identifica a una persona usuaria.") from exc
        return AuthUser(id=user_id, email=claims.get("email"))

    def _decode(self, token: str) -> dict:
        algorithm = jwt.get_unverified_header(token).get("alg")
        if self._jwks and algorithm in ASYMMETRIC_ALGORITHMS:
            key = self._jwks.get_signing_key_from_jwt(token).key
            algorithms = list(ASYMMETRIC_ALGORITHMS)
        elif (
            self._settings.supabase_jwt_secret
            and algorithm == "HS256"
            and self._settings.environment != "production"
        ):
            key = self._settings.supabase_jwt_secret.get_secret_value()
            algorithms = ["HS256"]
        else:
            raise jwt.InvalidAlgorithmError("Algoritmo no permitido")
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=self._settings.supabase_jwt_audience,
            issuer=self._expected_issuer(),
            options={"require": REQUIRED_CLAIMS},
        )

    def _expected_issuer(self) -> str | None:
        if not self._settings.supabase_url:
            return None
        return f"{self._settings.supabase_url.rstrip('/')}/auth/v1"


@lru_cache
def get_token_verifier() -> TokenVerifier:
    return TokenVerifier(get_settings())
