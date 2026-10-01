"""Proceso worker: ``python -m app.worker.main``."""

import logging
import signal
import socket
import time
import uuid
from datetime import datetime, timedelta
from types import FrameType

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Job
from app.worker import queue
from app.worker.handlers import HANDLERS
from app.worker.queue import DeferError, RetryLaterError

logger = logging.getLogger("app.worker")


def run_once(worker_id: str = "worker", now: datetime | None = None) -> bool:
    """Procesa a lo sumo un trabajo. Devuelve si procesó alguno."""
    now = now or utcnow()
    with session_scope() as db:
        queue.recover_stale(db, now, get_settings().job_lock_timeout_seconds)
        job = queue.claim_next(db, worker_id, now)
        if job is None:
            return False
        job_id, kind, payload = job.id, job.kind, dict(job.payload)

    handler = HANDLERS.get(kind)
    try:
        if handler is None:
            raise LookupError(f"Tipo de trabajo desconocido: {kind}")
        result = handler(payload)
    except DeferError as exc:
        _requeue(job_id, now, exc.delay_seconds, exc.reason, consume_attempt=False)
    except RetryLaterError as exc:
        _retry_or_fail(job_id, now, exc.reason, exc.delay_seconds)
    except Exception as exc:  # noqa: BLE001 - un trabajo defectuoso no debe tumbar el worker
        # Solo el tipo: el mensaje de la excepción podría contener datos personales.
        logger.exception("Trabajo %s (%s) falló: %s", job_id, kind, type(exc).__name__)
        _retry_or_fail(job_id, now, type(exc).__name__, None)
    else:
        _finish(job_id, "succeeded", result or {}, None)
    return True


def run_pending(now: datetime | None = None, limit: int = 100) -> int:
    """Procesa los trabajos vencidos. Útil en pruebas y en demostraciones."""
    processed = 0
    while processed < limit and run_once(now=now):
        processed += 1
    return processed


def _finish(job_id: uuid.UUID, status: str, result: dict | None, error: str | None) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:  # se eliminó junto con su propietario
            return
        job.status = status
        job.result = result
        job.last_error = error
        job.locked_at = None
        job.locked_by = None
        job.finished_at = utcnow()


def _requeue(
    job_id: uuid.UUID, now: datetime, delay_seconds: int, reason: str, *, consume_attempt: bool
) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = "queued"
        job.run_at = now + timedelta(seconds=delay_seconds)
        job.last_error = reason[:300]
        job.locked_at = None
        job.locked_by = None
        if not consume_attempt:
            job.attempts = max(job.attempts - 1, 0)


def _retry_or_fail(
    job_id: uuid.UUID, now: datetime, reason: str, delay_seconds: int | None
) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        attempts, max_attempts = job.attempts, job.max_attempts
    if attempts >= max_attempts:
        _finish(job_id, "failed", None, reason[:300])
        return
    delay = delay_seconds if delay_seconds is not None else queue.backoff_seconds(attempts)
    _requeue(job_id, now, delay, reason, consume_attempt=True)


class _Stop:
    requested = False

    def __call__(self, _signum: int, _frame: FrameType | None) -> None:
        self.requested = True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    stop = _Stop()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("Worker %s iniciado", worker_id)
    next_maintenance = utcnow()
    while not stop.requested:
        if utcnow() >= next_maintenance:
            with session_scope() as db:
                queue.enqueue(db, "maintenance", {}, idempotency_key=_maintenance_key())
            next_maintenance = utcnow() + timedelta(minutes=15)
        if not run_once(worker_id):
            time.sleep(settings.worker_poll_seconds)
    logger.info("Worker %s detenido", worker_id)


def _maintenance_key() -> str:
    now = utcnow()
    return f"maintenance:{now:%Y%m%d%H}:{now.minute // 15}"


if __name__ == "__main__":
    main()
