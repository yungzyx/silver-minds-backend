import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core.auth import TokenVerifier
from app.core.config import Settings
from app.core.errors import UnauthenticatedError
from tests.helpers import auth_headers, make_token

PROFILE = "/api/v1/profile"


def test_request_without_token_is_rejected(client: TestClient) -> None:
    response = client.get(PROFILE)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_valid_token_creates_profile_on_first_access(client: TestClient) -> None:
    user_id = uuid.uuid4()

    response = client.get(PROFILE, headers=auth_headers(user_id, email="rosa@example.com"))

    assert response.status_code == 200
    assert response.json()["id"] == str(user_id)
    assert response.json()["email"] == "rosa@example.com"


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=5)},
        {"aud": "otra-audiencia"},
        {"role": "anon"},
        {"role": "service_role"},
        {"sub": "no-es-un-uuid"},
        {"sub": None},
    ],
    ids=["vencido", "audiencia", "anon", "service_role", "sub-invalido", "sin-sub"],
)
def test_token_with_invalid_claims_is_rejected(client: TestClient, overrides: dict) -> None:
    response = client.get(PROFILE, headers=auth_headers(**overrides))

    assert response.status_code == 401


def test_token_signed_with_another_secret_is_rejected(client: TestClient) -> None:
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "role": "authenticated",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        "otro-secreto-que-no-es-el-del-servidor-0123456789",
        algorithm="HS256",
    )

    response = client.get(PROFILE, headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


def test_unsigned_token_is_rejected(client: TestClient) -> None:
    unsigned = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated"},
        key="",
        algorithm="none",
    )

    response = client.get(PROFILE, headers={"Authorization": f"Bearer {unsigned}"})

    assert response.status_code == 401


def test_malformed_authorization_header_is_rejected(client: TestClient) -> None:
    response = client.get(PROFILE, headers={"Authorization": "Basic abc"})

    assert response.status_code == 401


class _StubJwks:
    """Sustituye la descarga del JWKS por una clave pública conocida."""

    def __init__(self, public_key: ec.EllipticCurvePublicKey) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, _: str) -> object:
        return type("Key", (), {"key": self._public_key})()


def _jwks_verifier(public_key: ec.EllipticCurvePublicKey) -> TokenVerifier:
    settings = Settings(
        supabase_jwks_url="https://ref.supabase.co/auth/v1/.well-known/jwks.json",
        supabase_jwt_secret=None,
        supabase_url="https://ref.supabase.co",
    )
    verifier = TokenVerifier(settings)
    verifier._jwks = _StubJwks(public_key)
    return verifier


def _es256_token(private_key: ec.EllipticCurvePrivateKey, user_id: uuid.UUID, **overrides) -> str:
    claims = {
        "sub": str(user_id),
        "aud": "authenticated",
        "role": "authenticated",
        "iss": "https://ref.supabase.co/auth/v1",
        "exp": datetime.now(UTC) + timedelta(hours=1),
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="ES256")


def test_asymmetric_token_is_verified_against_jwks() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    user_id = uuid.uuid4()

    user = _jwks_verifier(private_key.public_key()).verify(_es256_token(private_key, user_id))

    assert user.id == user_id


def test_asymmetric_token_from_another_key_is_rejected() -> None:
    server_key = ec.generate_private_key(ec.SECP256R1())
    attacker_key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(UnauthenticatedError):
        _jwks_verifier(server_key.public_key()).verify(_es256_token(attacker_key, uuid.uuid4()))


def test_asymmetric_token_from_another_issuer_is_rejected() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    token = _es256_token(private_key, uuid.uuid4(), iss="https://otro.supabase.co/auth/v1")

    with pytest.raises(UnauthenticatedError):
        _jwks_verifier(private_key.public_key()).verify(token)


def test_hs256_token_is_rejected_when_only_jwks_is_configured() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(UnauthenticatedError):
        _jwks_verifier(private_key.public_key()).verify(make_token())
