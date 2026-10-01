from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.db import get_session_factory, session_scope
from app.models import Contact, Job
from app.worker import handlers, queue
from app.worker.main import run_once, run_pending
from app.worker.queue import DeferError, RetryLaterError
from tests.helpers import API, new_user


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch):
    """Registra un manejador de prueba y devuelve sus llamadas."""
    calls: list[dict] = []
    behavior = {"raise": None}

    def _handler(payload: dict) -> dict:
        calls.append(payload)
        if behavior["raise"] is not None:
            raise behavior["raise"]
        return {"done": True}

    monkeypatch.setitem(handlers.HANDLERS, "test_job", _handler)
    return calls, behavior


def enqueue(kind: str = "test_job", **kwargs: object) -> None:
    with session_scope() as db:
        queue.enqueue(db, kind, {"n": 1}, **kwargs)


def only_job(db: Session) -> Job:
    db.expire_all()
    return db.execute(select(Job)).scalar_one()


def test_job_runs_once_and_succeeds(db: Session, handler) -> None:
    calls, _ = handler
    enqueue()

    processed = run_pending()

    job = only_job(db)
    assert processed == 1 and len(calls) == 1
    assert job.status == "succeeded" and job.result == {"done": True}


def test_enqueue_with_same_idempotency_key_creates_one_job(db: Session, handler) -> None:
    calls, _ = handler
    enqueue(idempotency_key="k1")
    enqueue(idempotency_key="k1")

    run_pending()

    assert len(calls) == 1


def test_future_job_waits_until_due(db: Session, handler) -> None:
    calls, _ = handler
    enqueue(run_at=utcnow() + timedelta(hours=1))

    assert run_pending() == 0
    assert run_pending(now=utcnow() + timedelta(hours=2)) == 1
    assert len(calls) == 1


def test_failing_job_backs_off_and_fails_after_max_attempts(db: Session, handler) -> None:
    calls, behavior = handler
    behavior["raise"] = RuntimeError("dato sensible que no debe guardarse")
    enqueue(max_attempts=2)

    run_pending()
    after_first = only_job(db)
    first_status, first_run_at = after_first.status, after_first.run_at
    run_pending(now=utcnow() + timedelta(hours=1))

    job = only_job(db)
    assert first_status == "queued" and first_run_at > utcnow()
    assert job.status == "failed" and len(calls) == 2
    assert job.last_error == "RuntimeError"  # solo el tipo, nunca el mensaje


def test_retry_later_keeps_the_reason(db: Session, handler) -> None:
    _, behavior = handler
    behavior["raise"] = RetryLaterError("proveedor_no_disponible", delay_seconds=30)
    enqueue()

    run_pending()

    job = only_job(db)
    assert job.status == "queued" and job.last_error == "proveedor_no_disponible"


def test_deferred_job_does_not_consume_attempts(db: Session, handler) -> None:
    _, behavior = handler
    behavior["raise"] = DeferError("acciones_en_pausa", delay_seconds=60)
    enqueue(max_attempts=1)

    run_pending()

    job = only_job(db)
    assert job.status == "queued" and job.attempts == 0


def test_unknown_job_kind_fails_without_crashing_the_worker(db: Session) -> None:
    enqueue(kind="no_existe", max_attempts=1)

    run_pending()

    assert only_job(db).status == "failed"


def test_two_workers_never_claim_the_same_job(handler) -> None:
    enqueue()
    first, second = get_session_factory()(), get_session_factory()()
    try:
        claimed_by_first = queue.claim_next(first, "w1", utcnow())
        claimed_by_second = queue.claim_next(second, "w2", utcnow())  # fila bloqueada: la salta
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()

    assert claimed_by_first is not None
    assert claimed_by_second is None


def test_job_of_a_dead_worker_is_recovered(db: Session, handler) -> None:
    calls, _ = handler
    enqueue()
    with session_scope() as session:
        queue.claim_next(session, "worker-muerto", utcnow())  # reclamado y nunca terminado

    assert run_once() is False  # el bloqueo sigue vigente
    recovered = run_once(now=utcnow() + timedelta(minutes=10))

    assert recovered is True and len(calls) == 1
    assert only_job(db).status == "succeeded"


def test_worker_restart_mid_send_does_not_resend(
    client: TestClient, db: Session, providers
) -> None:
    """El worker murió después de marcar `sending` y antes de registrar el resultado."""
    _, headers = new_user(client)
    client.post(
        f"{API}/contacts", headers=headers, json={"name": "Camila", "email": "c@example.com"}
    )
    with session_scope() as session:
        queue.claim_next(session, "worker-muerto", utcnow())
        session.execute(select(Contact)).scalar_one().consent_delivery = "sending"

    run_pending(now=utcnow() + timedelta(minutes=10))

    db.expire_all()
    assert providers.email.sent == []
    assert db.execute(select(Contact)).scalar_one().consent_delivery == "send_uncertain"
