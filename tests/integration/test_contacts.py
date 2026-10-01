from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.db import session_scope
from app.core.errors import TokenExpiredError
from app.integrations.email.base import (
    EmailAmbiguousError,
    EmailRejectedError,
    EmailTransientError,
)
from app.models import Contact, Job
from app.modules.contacts import service
from app.worker.main import run_pending
from tests.helpers import API, link_token, new_user

HIJA = {"name": "Camila", "email": "camila@example.com", "relationship": "hija"}


def add_contact(client: TestClient, headers: dict, **overrides: object) -> dict:
    response = client.post(f"{API}/contacts", headers=headers, json={**HIJA, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def accepted_contact(client: TestClient, headers: dict, providers, **overrides: object) -> dict:
    contact = add_contact(client, headers, **overrides)
    run_pending()
    token = link_token(providers.email.sent[-1].text)
    client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})
    return contact


def test_new_contact_is_pending_and_receives_consent_email(client: TestClient, providers) -> None:
    _, headers = new_user(client, preferred_name="Rosa")

    contact = add_contact(client, headers)
    run_pending()

    [email] = providers.email.sent
    assert contact["status"] == "pending"
    assert email.to == "camila@example.com"
    assert "Rosa" in email.subject
    listed = client.get(f"{API}/contacts", headers=headers).json()["items"][0]
    assert listed["consent_delivery"] == "sent"


def test_opening_the_link_does_not_change_state(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    run_pending()
    token = link_token(providers.email.sent[0].text)

    for _ in range(3):  # un antivirus o la vista previa del correo visitan el enlace
        view = client.get(f"{API}/contact-consents/{token}")

    assert view.status_code == 200
    assert view.json()["status"] == "pending"
    assert client.get(f"{API}/contacts", headers=headers).json()["items"][0]["status"] == "pending"


def test_contact_accepts_with_post(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    run_pending()
    token = link_token(providers.email.sent[0].text)

    response = client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})
    repeated = client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})

    assert response.json()["status"] == "accepted"
    assert repeated.status_code == 200
    assert client.get(f"{API}/contacts", headers=headers).json()["items"][0]["status"] == "accepted"


def test_contact_declines_and_cannot_change_answer(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    run_pending()
    token = link_token(providers.email.sent[0].text)

    declined = client.post(f"{API}/contact-consents/{token}", json={"decision": "decline"})
    changed = client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})

    assert declined.json()["status"] == "declined"
    assert changed.status_code == 409


def test_expired_token_cannot_accept(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    run_pending()
    token = link_token(providers.email.sent[0].text)
    later = utcnow() + timedelta(days=8)

    with session_scope() as db:
        view = service.consent_view(db, token, now=later)
        with pytest.raises(TokenExpiredError):
            service.decide_consent(db, token, "accept", now=later)

    assert view.expired is True
    assert client.get(f"{API}/contacts", headers=headers).json()["items"][0]["status"] == "pending"


def test_expired_token_returns_410_over_http(client: TestClient, providers, db: Session) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    run_pending()
    token = link_token(providers.email.sent[0].text)
    contact = db.execute(select(Contact)).scalar_one()
    contact.consent_expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    response = client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "token_expired"


def test_unknown_token_is_not_found(client: TestClient) -> None:
    assert client.get(f"{API}/contact-consents/no-existe").status_code == 404
    response = client.post(f"{API}/contact-consents/no-existe", json={"decision": "accept"})
    assert response.status_code == 404


def test_duplicate_active_email_is_a_conflict(client: TestClient) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)

    response = client.post(
        f"{API}/contacts", headers=headers, json={**HIJA, "email": "CAMILA@example.com"}
    )

    assert response.status_code == 409


def test_invalid_email_is_rejected(client: TestClient) -> None:
    _, headers = new_user(client)

    response = client.post(f"{API}/contacts", headers=headers, json={**HIJA, "email": "no-es"})

    assert response.status_code == 422


def test_revoking_before_the_worker_runs_cancels_the_email(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    contact = add_contact(client, headers)

    revoked = client.post(f"{API}/contacts/{contact['id']}/revoke", headers=headers)
    run_pending()

    assert revoked.json()["status"] == "revoked"
    assert providers.email.sent == []


def test_revoked_contact_link_stops_working(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    contact = add_contact(client, headers)
    run_pending()
    token = link_token(providers.email.sent[0].text)

    client.post(f"{API}/contacts/{contact['id']}/revoke", headers=headers)
    response = client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})

    assert response.status_code == 404
    assert client.get(f"{API}/contacts", headers=headers).json()["items"][0]["status"] == "revoked"


def test_contacts_are_isolated_between_users(client: TestClient) -> None:
    _, rosa = new_user(client)
    _, pedro = new_user(client)
    contact = add_contact(client, rosa)

    assert client.get(f"{API}/contacts", headers=pedro).json()["total"] == 0
    assert (
        client.patch(f"{API}/contacts/{contact['id']}", headers=pedro, json={"name": "X"})
    ).status_code == 404
    assert client.post(f"{API}/contacts/{contact['id']}/revoke", headers=pedro).status_code == 404
    # El mismo correo puede ser contacto de dos personas distintas.
    assert client.post(f"{API}/contacts", headers=pedro, json=HIJA).status_code == 201


def test_rejected_email_is_not_retried(client: TestClient, providers, db: Session) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    providers.email.fail_next_with = EmailRejectedError("dirección inválida")

    run_pending()

    job = db.execute(select(Job).where(Job.kind == "send_contact_consent")).scalar_one()
    assert job.status == "succeeded" and job.result == {"outcome": "failed"}
    assert (
        client.get(f"{API}/contacts", headers=headers).json()["items"][0]["consent_delivery"]
        == "failed"
    )


def test_transient_email_failure_is_retried_and_delivered_once(
    client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    providers.email.fail_next_with = EmailTransientError("conexión rechazada")

    run_pending()
    job = db.execute(select(Job).where(Job.kind == "send_contact_consent")).scalar_one()
    queued_again = job.status == "queued"
    run_pending(now=utcnow() + timedelta(minutes=5))

    assert queued_again
    assert len(providers.email.sent) == 1


def test_ambiguous_email_outcome_is_not_resent(client: TestClient, providers, db: Session) -> None:
    _, headers = new_user(client)
    add_contact(client, headers)
    providers.email.fail_next_with = EmailAmbiguousError("timeout")

    run_pending()
    run_pending(now=utcnow() + timedelta(hours=2))

    assert providers.email.sent == []
    assert (
        client.get(f"{API}/contacts", headers=headers).json()["items"][0]["consent_delivery"]
        == "send_uncertain"
    )
    job = db.execute(select(Job).where(Job.kind == "send_contact_consent")).scalar_one()
    assert job.status == "succeeded"
