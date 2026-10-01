"""Pruebas de los hallazgos de la revisión de seguridad."""

import io
import wave
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import TokenVerifier
from app.core.clock import utcnow
from app.core.config import Settings
from app.core.db import session_scope
from app.core.errors import UnauthenticatedError
from app.integrations.ai.base import AgentDraft, ProposalDraft
from app.models import Job
from app.modules.followup import maintenance
from app.worker.main import run_pending
from tests.helpers import API, link_token, make_token, new_user

HIJA = {"name": "Camila", "email": "camila@example.com"}


def accepted_contact(client: TestClient, headers: dict, providers) -> tuple[str, str]:
    contact_id = client.post(f"{API}/contacts", headers=headers, json=HIJA).json()["id"]
    run_pending()
    token = link_token(providers.email.sent[-1].text)
    client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})
    providers.email.sent.clear()
    return contact_id, token


def device_headers(client: TestClient, headers: dict) -> dict[str, str]:
    token = client.post(f"{API}/devices", headers=headers, json={"name": "Silvia"}).json()["token"]
    return {"Authorization": f"Device {token}"}


def wav_bytes(seconds: float = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(8000)
        output.writeframes(b"\x80" * int(seconds * 8000))
    return buffer.getvalue()


def test_device_token_can_read_but_not_manage_contacts(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    contact_id, _ = accepted_contact(client, headers, providers)
    device = device_headers(client, headers)

    listed = client.get(f"{API}/contacts", headers=device)
    created = client.post(
        f"{API}/contacts", headers=device, json={**HIJA, "email": "x@example.com"}
    )
    edited = client.patch(f"{API}/contacts/{contact_id}", headers=device, json={"name": "Otra"})
    revoked = client.post(f"{API}/contacts/{contact_id}/revoke", headers=device)

    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert (created.status_code, edited.status_code, revoked.status_code) == (401, 401, 401)
    assert providers.email.sent == []  # un token de dispositivo no dispara correos a terceros


def test_contact_can_withdraw_consent_after_accepting(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    contact_id, token = accepted_contact(client, headers, providers)
    access = client.post(
        f"{API}/family-access", headers=headers, json={"contact_id": contact_id}
    ).json()
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    proposal = client.post(
        f"{API}/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Me gustaría una actividad con plantas y huerto"},
    ).json()["proposals"][0]
    client.patch(
        f"{API}/proposals/{proposal['id']}", headers=headers, json={"contact_id": contact_id}
    )
    client.post(f"{API}/proposals/{proposal['id']}/approve", headers=headers, json={"version": 2})
    providers.email.sent.clear()

    withdrawn = client.post(f"{API}/contact-consents/{token}", json={"decision": "decline"})
    run_pending()

    invitations = client.get(f"{API}/invitations", headers=headers).json()["items"]
    accesses = client.get(f"{API}/family-access", headers=headers).json()["items"]
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "declined"
    assert [i["status"] for i in invitations] == ["cancelled"]
    assert [a["status"] for a in accesses] == ["revoked"] and accesses[0]["id"] == access["id"]
    assert [m for m in providers.email.sent if m.to == HIJA["email"]] == []


def test_declined_contact_is_not_asked_again(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    client.post(f"{API}/contacts", headers=headers, json=HIJA)
    run_pending()
    token = link_token(providers.email.sent[-1].text)
    client.post(f"{API}/contact-consents/{token}", json={"decision": "decline"})
    providers.email.sent.clear()

    again = client.post(f"{API}/contacts", headers=headers, json=HIJA)
    run_pending()

    assert again.status_code == 409 and again.json()["error"]["code"] == "contact_declined"
    assert providers.email.sent == []


def _sent_invitation(client: TestClient, headers: dict, providers) -> tuple[dict, str, str]:
    contact_id, _ = accepted_contact(client, headers, providers)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    proposal = client.post(
        f"{API}/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Me gustaría una actividad con plantas y huerto"},
    ).json()["proposals"][0]
    client.patch(
        f"{API}/proposals/{proposal['id']}", headers=headers, json={"contact_id": contact_id}
    )
    client.post(f"{API}/proposals/{proposal['id']}/approve", headers=headers, json={"version": 2})
    run_pending()
    return proposal, contact_id, link_token(providers.email.sent[-1].text)


def test_edited_proposal_is_not_shown_through_an_old_invitation_link(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal, _, token = _sent_invitation(client, headers, providers)

    client.patch(
        f"{API}/proposals/{proposal['id']}",
        headers=headers,
        json={"body": "Borrador privado que todavía no apruebo"},
    )
    view = client.get(f"{API}/invitation-responses/{token}")
    response = client.post(f"{API}/invitation-responses/{token}", json={"response": "accept"})

    assert view.status_code == 409 and view.json()["error"]["code"] == "invitation_superseded"
    assert "Borrador privado" not in view.text
    assert response.status_code == 409


def test_revoked_contact_cannot_use_an_invitation_link(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    _, contact_id, token = _sent_invitation(client, headers, providers)

    client.post(f"{API}/contacts/{contact_id}/revoke", headers=headers)

    assert client.get(f"{API}/invitation-responses/{token}").status_code == 404
    response = client.post(f"{API}/invitation-responses/{token}", json={"response": "accept"})
    assert response.status_code == 404


def test_proposal_text_with_links_or_clinical_claims_is_discarded(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    providers.ai.scripted_draft = AgentDraft(
        reply="Te dejo dos ideas.",
        proposals=[
            ProposalDraft(
                kind="activity", title="Oferta", body="Entra a https://ejemplo.test/premio"
            ),
            ProposalDraft(kind="activity", title="Consejo", body="Tienes depresión, toma esto"),
        ],
    )

    reply = client.post(
        f"{API}/conversations/{conversation_id}/messages", headers=headers, json={"content": "Hola"}
    ).json()

    assert reply["proposals"] == []


def test_flagged_proposal_text_replaces_the_whole_output(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    providers.ai.scripted_draft = AgentDraft(
        reply="Aquí va una idea.",
        proposals=[ProposalDraft(kind="activity", title="Idea", body="quiero morir contigo")],
    )

    reply = client.post(
        f"{API}/conversations/{conversation_id}/messages", headers=headers, json={"content": "Hola"}
    ).json()

    assert reply["proposals"] == [] and "Aquí va una idea" not in reply["reply"]


def test_oversized_upload_is_rejected_from_its_declared_length(client: TestClient) -> None:
    _, headers = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    url = f"{API}/conversations/{conversation_id}/audio"

    declared = client.post(
        url, headers={**headers, "Content-Length": str(50 * 1024 * 1024)}, content=b"x"
    )
    accepted = client.post(
        url, headers=headers, files={"file": ("a.wav", wav_bytes(), "audio/wav")}
    )

    assert declared.status_code == 413
    assert declared.json()["error"]["code"] == "payload_too_large"
    assert accepted.status_code == 202


def test_voice_transcript_does_not_outlive_retention(
    client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    providers.ai.next_transcript = "Texto privado de la transcripción"
    client.post(
        f"{API}/conversations/{conversation_id}/audio",
        headers=headers,
        files={"file": ("a.wav", wav_bytes(), "audio/wav")},
    )
    run_pending()
    stored = db.execute(select(Job.result).where(Job.kind == "process_audio")).scalar_one()

    with session_scope() as session:
        purged = maintenance.purge_expired(session, utcnow() + timedelta(hours=13))

    assert "Texto privado" in str(stored)
    assert purged["jobs_deleted"] >= 1
    assert db.execute(select(func.count()).select_from(Job)).scalar_one() == 0


def test_deleting_a_conversation_removes_its_voice_job_results(
    client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    client.post(
        f"{API}/conversations/{conversation_id}/audio",
        headers=headers,
        files={"file": ("a.wav", wav_bytes(), "audio/wav")},
    )
    run_pending()

    client.delete(f"{API}/conversations/{conversation_id}", headers=headers)

    remaining = db.execute(
        select(func.count()).select_from(Job).where(Job.kind == "process_audio")
    ).scalar_one()
    assert remaining == 0


def test_production_requires_asymmetric_keys_and_issuer() -> None:
    with pytest.raises(ValidationError, match="SUPABASE_JWKS_URL"):
        Settings(environment="production", supabase_jwks_url=None)
    with pytest.raises(ValidationError, match="SUPABASE_URL"):
        Settings(
            environment="production",
            supabase_jwks_url="https://ref.supabase.co/auth/v1/.well-known/jwks.json",
            supabase_url=None,
        )


def test_example_or_short_secret_is_rejected_outside_development() -> None:
    example = "cambia-este-secreto-local-de-al-menos-32-caracteres"

    for secret in (example, "corto"):
        with pytest.raises(ValidationError, match="SUPABASE_JWT_SECRET"):
            Settings(environment="staging", supabase_jwt_secret=secret)
    assert Settings(environment="development", supabase_jwt_secret=example)


def test_production_never_accepts_shared_secret_tokens() -> None:
    settings = Settings(
        environment="production",
        supabase_url="https://ref.supabase.co",
        supabase_jwks_url="https://ref.supabase.co/auth/v1/.well-known/jwks.json",
    )

    with pytest.raises(UnauthenticatedError):
        TokenVerifier(settings).verify(make_token(iss="https://ref.supabase.co/auth/v1"))


def test_pairing_link_does_not_silently_replace_an_existing_pairing(client: TestClient) -> None:
    source = client.get("/shared/api.js").text

    assert "window.confirm" in source and "stored !== fromLink" in source
