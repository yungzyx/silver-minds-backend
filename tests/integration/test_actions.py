import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.db import session_scope
from app.integrations.email.base import EmailAmbiguousError, EmailTransientError
from app.models import Conversation, Invitation, Job, Reminder, SafetyEvent
from app.modules.followup import maintenance
from app.worker import queue
from app.worker.main import run_pending
from tests.helpers import API, link_token, new_user

WANT_PLANTS = "Me gustaría una actividad con plantas y huerto"


def accepted_contact(client: TestClient, headers: dict, providers, name: str = "Camila") -> str:
    contact_id = client.post(
        f"{API}/contacts",
        headers=headers,
        json={"name": name, "email": f"{name.lower()}@example.com"},
    ).json()["id"]
    run_pending()
    token = link_token(providers.email.sent[-1].text)
    client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})
    providers.email.sent.clear()
    return contact_id


def draft_proposal(client: TestClient, headers: dict, message: str = WANT_PLANTS) -> dict:
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    reply = client.post(
        f"{API}/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": message},
    ).json()
    return reply["proposals"][0]


def approve(client: TestClient, headers: dict, proposal: dict, version: int | None = None):
    return client.post(
        f"{API}/proposals/{proposal['id']}/approve",
        headers=headers,
        json={"version": version or proposal["version"]},
    )


def ready_invitation(client: TestClient, headers: dict, providers, **edit: object) -> dict:
    """Propuesta en borrador con destinatario elegido por la persona."""
    contact_id = accepted_contact(client, headers, providers)
    proposal = draft_proposal(client, headers)
    return client.patch(
        f"{API}/proposals/{proposal['id']}",
        headers=headers,
        json={"contact_id": contact_id, **edit},
    ).json()


def invitation_status(client: TestClient, headers: dict) -> list[str]:
    items = client.get(f"{API}/invitations", headers=headers).json()["items"]
    return [item["status"] for item in items]


def test_nothing_is_sent_while_the_proposal_is_a_draft(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    ready_invitation(client, headers, providers)

    run_pending()

    assert providers.email.sent == []
    assert invitation_status(client, headers) == []


def test_approval_sends_the_invitation_with_details_from_the_record(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client, preferred_name="Rosa")
    proposal = ready_invitation(client, headers, providers)

    response = approve(client, headers, proposal)
    run_pending()

    [email] = providers.email.sent
    assert response.json()["status"] == "approved"
    assert email.to == "camila@example.com" and "Rosa te invita" in email.subject
    assert "Martes de 10:00 a 11:30" in email.text  # horario del registro vigente
    assert invitation_status(client, headers) == ["sent"]


def test_approval_requires_the_reviewed_version(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)

    stale = approve(client, headers, proposal, version=proposal["version"] - 1)

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_mismatch"


def test_approving_twice_does_not_duplicate_the_invitation(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)

    again = approve(client, headers, proposal)
    run_pending()

    assert again.status_code == 409
    assert len(providers.email.sent) == 1
    assert db.execute(select(func.count()).select_from(Invitation)).scalar_one() == 1


def test_running_the_same_send_job_twice_delivers_once(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    invitation_id = str(db.execute(select(Invitation.id)).scalar_one())
    run_pending()

    with session_scope() as session:  # un segundo trabajo para la misma invitación
        queue.enqueue(session, "send_invitation", {"invitation_id": invitation_id})
    run_pending()

    assert len(providers.email.sent) == 1


def test_editing_an_approved_proposal_requires_new_approval(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)

    edited = client.patch(
        f"{API}/proposals/{proposal['id']}", headers=headers, json={"body": "¿Vamos juntas?"}
    ).json()
    run_pending()  # la invitación de la versión anterior no debe salir

    assert edited["status"] == "draft" and edited["version"] == proposal["version"] + 1
    assert providers.email.sent == []
    assert invitation_status(client, headers) == ["cancelled"]

    approve(client, headers, edited)
    run_pending()

    [email] = providers.email.sent
    assert "¿Vamos juntas?" in email.text


def test_contact_must_have_accepted_to_receive_invitations(
    knowledge: None, client: TestClient
) -> None:
    _, headers = new_user(client)
    pending = client.post(
        f"{API}/contacts", headers=headers, json={"name": "Hijo", "email": "hijo@example.com"}
    ).json()["id"]
    proposal = draft_proposal(client, headers)

    response = client.patch(
        f"{API}/proposals/{proposal['id']}", headers=headers, json={"contact_id": pending}
    )

    assert response.status_code == 422


def test_revoking_the_contact_before_the_worker_runs_cancels_the_send(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)

    client.post(f"{API}/contacts/{proposal['contact_id']}/revoke", headers=headers)
    run_pending()

    assert providers.email.sent == []
    assert invitation_status(client, headers) == ["cancelled"]


def test_worker_rechecks_contact_permission_at_send_time(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    """Aunque la invitación siga en cola, el worker no envía a un contacto que ya no acepta."""
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    from app.models import Contact

    db.execute(select(Contact)).scalar_one().status = "revoked"  # revocación sin cancelar la cola
    db.commit()

    run_pending()

    assert providers.email.sent == []
    assert invitation_status(client, headers) == ["cancelled"]


def test_cancelling_an_approved_proposal_cancels_pending_sends(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)

    cancelled = client.post(f"{API}/proposals/{proposal['id']}/cancel", headers=headers)
    run_pending()

    assert cancelled.json()["status"] == "cancelled"
    assert providers.email.sent == []


def test_support_route_blocks_approval_and_pauses_queued_sends(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    other = draft_proposal(client, headers, "Quiero una actividad para tejer a crochet")
    approve(client, headers, proposal)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    client.post(
        f"{API}/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Ya no quiero seguir viviendo"},
    )

    blocked = approve(client, headers, other)
    run_pending()

    job = db.execute(select(Job).where(Job.kind == "send_invitation")).scalar_one()
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "actions_paused"
    assert providers.email.sent == []
    assert invitation_status(client, headers) == ["queued"]  # en pausa, no cancelada
    assert job.status == "queued" and job.attempts == 0 and job.last_error == "actions_paused"


def test_paused_send_goes_out_after_contextual_resume(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    for content in ("Ya no quiero seguir viviendo", "Hablé con mi hija y estoy más tranquila"):
        client.post(
            f"{API}/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": content},
        )
    run_pending()
    sent_while_paused = len(providers.email.sent)

    client.post(f"{API}/support/resume", headers=headers, json={"confirm": True})
    run_pending(now=utcnow() + timedelta(minutes=10))

    assert sent_while_paused == 0
    assert len(providers.email.sent) == 1


def test_opening_the_invitation_link_does_not_change_state(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client, preferred_name="Rosa")
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    run_pending()
    token = link_token(providers.email.sent[0].text)

    for _ in range(3):  # visitas automáticas de un antivirus o de la vista previa
        view = client.get(f"{API}/invitation-responses/{token}")

    assert view.json()["status"] == "sent" and view.json()["inviter_name"] == "Rosa"
    assert invitation_status(client, headers) == ["sent"]


def test_contact_accepts_the_invitation_with_post(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    run_pending()
    token = link_token(providers.email.sent[0].text)

    accepted = client.post(f"{API}/invitation-responses/{token}", json={"response": "accept"})
    repeated = client.post(f"{API}/invitation-responses/{token}", json={"response": "accept"})
    changed = client.post(f"{API}/invitation-responses/{token}", json={"response": "decline"})

    assert accepted.json()["status"] == "accepted" and repeated.status_code == 200
    assert changed.status_code == 409
    assert invitation_status(client, headers) == ["accepted"]


def test_expired_invitation_token_returns_410(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    run_pending()
    token = link_token(providers.email.sent[0].text)
    db.execute(select(Invitation)).scalar_one().token_expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    response = client.post(f"{API}/invitation-responses/{token}", json={"response": "accept"})

    assert response.status_code == 410
    assert client.get(f"{API}/invitation-responses/{token}").json()["expired"] is True
    assert invitation_status(client, headers) == ["sent"]


def test_ambiguous_provider_outcome_is_not_resent(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    providers.email.fail_next_with = EmailAmbiguousError("timeout")

    run_pending()
    run_pending(now=utcnow() + timedelta(hours=3))

    assert providers.email.sent == []
    assert invitation_status(client, headers) == ["send_uncertain"]


def test_transient_failure_retries_with_the_same_idempotency_key(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    providers.email.fail_next_with = EmailTransientError("conexión rechazada")

    run_pending()
    run_pending(now=utcnow() + timedelta(minutes=5))

    [email] = providers.email.sent
    assert email.idempotency_key == f"invitation:{proposal['id']}:{proposal['version']}"
    assert invitation_status(client, headers) == ["sent"]


def test_worker_restart_mid_send_marks_the_invitation_uncertain(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)
    with session_scope() as session:  # el worker reclamó el trabajo, marcó sending y murió
        queue.claim_next(session, "worker-muerto", utcnow())
        session.execute(select(Invitation)).scalar_one().status = "sending"

    run_pending(now=utcnow() + timedelta(minutes=10))

    assert providers.email.sent == []
    assert invitation_status(client, headers) == ["send_uncertain"]


def test_scheduled_proposal_gets_reminder_and_followup(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client, preferred_name="Rosa")
    when = utcnow() + timedelta(days=3)
    proposal = ready_invitation(client, headers, providers, scheduled_for=when.isoformat())
    approve(client, headers, proposal)
    run_pending()
    providers.email.sent.clear()

    early = run_pending(now=when - timedelta(hours=30))
    run_pending(now=when - timedelta(hours=23))

    reminders = client.get(f"{API}/reminders", headers=headers).json()["items"]
    [email] = providers.email.sent
    assert early == 0
    assert email.subject.startswith("Recordatorio") and "Rosa" in email.text
    assert {(r["kind"], r["status"]) for r in reminders} == {
        ("reminder", "sent"),
        ("followup", "pending"),
    }


def test_feedback_completes_the_proposal_and_closes_the_followup(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    proposal = ready_invitation(client, headers, providers)
    approve(client, headers, proposal)

    feedback = client.post(
        f"{API}/proposals/{proposal['id']}/feedback",
        headers=headers,
        json={"happened": True, "rating": 5, "comment": "Lo pasamos bien"},
    )

    reminders = client.get(f"{API}/reminders", headers=headers).json()["items"]
    detail = client.get(f"{API}/proposals/{proposal['id']}", headers=headers).json()
    assert feedback.status_code == 201
    assert detail["status"] == "completed"
    assert [r["status"] for r in reminders if r["kind"] == "followup"] == ["done"]


def test_rejecting_a_draft(knowledge: None, client: TestClient) -> None:
    _, headers = new_user(client)
    proposal = draft_proposal(client, headers)

    rejected = client.post(f"{API}/proposals/{proposal['id']}/reject", headers=headers)
    late_approval = approve(client, headers, proposal)

    assert rejected.json()["status"] == "rejected"
    assert late_approval.status_code == 409


def test_activities_list_only_current_approved_records(knowledge: None, client: TestClient) -> None:
    _, headers = new_user(client)

    listed = client.get(f"{API}/activities", headers=headers).json()

    titles = {item["title"] for item in listed["items"]}
    assert listed["total"] == 13
    assert "Paseo al museo" not in titles and "Feria de trueque de plantas" not in titles


def test_proposals_and_invitations_are_isolated_between_users(
    knowledge: None, client: TestClient, providers
) -> None:
    _, rosa = new_user(client)
    _, pedro = new_user(client)
    proposal = ready_invitation(client, rosa, providers)
    pedro_contact = accepted_contact(client, pedro, providers, name="Ana")

    assert client.get(f"{API}/proposals", headers=pedro).json()["total"] == 0
    assert client.get(f"{API}/invitations", headers=pedro).json()["total"] == 0
    for action, body in [
        ("approve", {"version": proposal["version"]}),
        ("reject", None),
        ("cancel", None),
        ("feedback", {"happened": True}),
    ]:
        kwargs = {"json": body} if body else {}
        response = client.post(
            f"{API}/proposals/{proposal['id']}/{action}", headers=pedro, **kwargs
        )
        assert response.status_code == 404, action
    # Rosa no puede dirigir su propuesta a un contacto de Pedro.
    foreign = client.patch(
        f"{API}/proposals/{proposal['id']}", headers=rosa, json={"contact_id": pedro_contact}
    )
    assert foreign.status_code == 404


def test_maintenance_purges_old_conversations_and_safety_events(
    client: TestClient, db: Session
) -> None:
    user_id, headers = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    client.post(
        f"{API}/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Ya no quiero seguir viviendo"},
    )

    with session_scope() as session:
        kept = maintenance.purge_expired(session, utcnow() + timedelta(days=29))
    with session_scope() as session:
        purged = maintenance.purge_expired(session, utcnow() + timedelta(days=31))

    assert kept == {"conversations_deleted": 0, "safety_events_deleted": 0}
    assert purged == {"conversations_deleted": 1, "safety_events_deleted": 1}
    assert db.execute(select(func.count()).select_from(Conversation)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(SafetyEvent)).scalar_one() == 0
    assert uuid.UUID(conversation_id)
    assert db.execute(select(func.count()).select_from(Reminder)).scalar_one() == 0
