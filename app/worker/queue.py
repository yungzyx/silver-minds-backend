"""Cola persistente en PostgreSQL."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import Job

MAX_BACKOFF_SECONDS = 3600


class RetryLaterError(Exception):
    """El trabajo no pudo completarse ahora; consume un intento."""

    def __init__(self, reason: str, delay_seconds: int = 60) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay_seconds = delay_seconds


class DeferError(Exception):
    """El trabajo debe esperar (p. ej. acciones en pausa). No consume intentos."""

    def __init__(self, reason: str, delay_seconds: int = 300) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay_seconds = delay_seconds


def enqueue(
    db: Session,
    kind: str,
    payload: dict,
    *,
    owner_id: uuid.UUID | None = None,
    run_at: datetime | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 5,
) -> uuid.UUID:
    """Encola un trabajo. Con ``idempotency_key`` repetida devuelve el trabajo existente."""
    job_id = uuid.uuid4()
    statement = insert(Job).values(
        id=job_id,
        kind=kind,
        payload=payload,
        owner_id=owner_id,
        run_at=run_at or utcnow(),
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        status="queued",
        attempts=0,
    )
    if idempotency_key is None:
        db.execute(statement)
        return job_id
    inserted = db.execute(
        statement.on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(Job.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    return db.execute(select(Job.id).where(Job.idempotency_key == idempotency_key)).scalar_one()


def claim_next(db: Session, worker_id: str, now: datetime) -> Job | None:
    """Reclama el siguiente trabajo. SKIP LOCKED permite varios workers a la vez."""
    job = db.execute(
        select(Job)
        .where(Job.status == "queued", Job.run_at <= now)
        .order_by(Job.run_at, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.locked_at = now
    job.locked_by = worker_id
    job.attempts += 1
    return job


def recover_stale(db: Session, now: datetime, timeout_seconds: int) -> int:
    """Devuelve a la cola los trabajos cuyo worker desapareció.

    Reencolar es seguro: cada manejador comprueba el estado de su entidad y nunca
    repite un envío que pudo haber salido.
    """
    result = db.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < now - timedelta(seconds=timeout_seconds))
        .values(status="queued", locked_at=None, locked_by=None, run_at=now)
    )
    return result.rowcount


def backoff_seconds(attempts: int) -> int:
    return min(60 * 2 ** max(attempts - 1, 0), MAX_BACKOFF_SECONDS)
