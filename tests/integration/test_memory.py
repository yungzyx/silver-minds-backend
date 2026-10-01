import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.integrations.ai.base import MemoryDraft
from app.models import ConfirmedMemory, Job, MemoryCandidate, MemoryEmbedding
from app.modules.memory import service
from app.worker.main import run_pending
from tests.helpers import API, new_user


def propose(owner_id: uuid.UUID, content: str, category: str = "schedule") -> str:
    """Simula al agente proponiendo una memoria candidata."""
    with session_scope() as db:
        [candidate] = service.propose_candidates(
            db, owner_id, None, [MemoryDraft(category=category, content=content)]
        )
        return str(candidate.id)


def count(db: Session, model: type) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_candidate_is_not_a_memory_until_confirmed(client: TestClient) -> None:
    user_id, headers = new_user(client)
    propose(user_id, "Prefiere actividades por la mañana")

    candidates = client.get(f"{API}/memory-candidates", headers=headers).json()
    memories = client.get(f"{API}/memories", headers=headers).json()

    assert candidates["total"] == 1
    assert memories["total"] == 0


def test_confirming_creates_memory_and_indexes_it(client: TestClient, db: Session) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Prefiere actividades por la mañana")

    response = client.post(f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers)
    run_pending()

    memory_id = response.json()["id"]
    assert response.status_code == 201
    assert response.json()["version"] == 1
    assert client.get(f"{API}/memory-candidates", headers=headers).json()["total"] == 0
    embedding = db.get(MemoryEmbedding, uuid.UUID(memory_id))
    assert embedding is not None and embedding.memory_version == 1


def test_candidate_can_be_edited_before_confirming(client: TestClient) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Prefiere las mañanas")

    edited = client.patch(
        f"{API}/memory-candidates/{candidate_id}",
        headers=headers,
        json={"content": "Prefiere salir después de las 10"},
    )
    confirmed = client.post(
        f"{API}/memory-candidates/{candidate_id}/confirm",
        headers=headers,
        json={"category": "schedule"},
    )

    assert edited.status_code == 200
    assert confirmed.json()["content"] == "Prefiere salir después de las 10"


def test_rejecting_deletes_the_candidate(client: TestClient, db: Session) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta el jazz", "interest")

    response = client.post(f"{API}/memory-candidates/{candidate_id}/reject", headers=headers)

    assert response.status_code == 204
    assert count(db, MemoryCandidate) == 0
    assert count(db, ConfirmedMemory) == 0


def test_confirming_twice_is_a_conflict(client: TestClient) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta el jazz", "interest")
    client.post(f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers)

    again = client.post(f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers)

    assert again.status_code == 409


def test_duplicate_candidates_are_not_proposed_again(client: TestClient, db: Session) -> None:
    user_id, _ = new_user(client)
    propose(user_id, "Le gusta el jazz", "interest")

    with session_scope() as session:
        created = service.propose_candidates(
            session, user_id, None, [MemoryDraft(category="interest", content="le gusta el JAZZ")]
        )

    assert created == []
    assert count(db, MemoryCandidate) == 1


def test_deleting_memory_removes_its_embedding(client: TestClient, db: Session) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta la jardinería", "interest")
    memory_id = client.post(
        f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers
    ).json()["id"]
    run_pending()
    assert count(db, MemoryEmbedding) == 1

    response = client.delete(f"{API}/memories/{memory_id}", headers=headers)

    assert response.status_code == 204
    assert count(db, ConfirmedMemory) == 0
    assert count(db, MemoryEmbedding) == 0


def test_pending_indexing_does_not_recreate_a_deleted_memory(
    client: TestClient, db: Session
) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta la jardinería", "interest")
    memory_id = client.post(
        f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers
    ).json()["id"]
    client.delete(f"{API}/memories/{memory_id}", headers=headers)  # el trabajo sigue en cola

    run_pending()

    job = db.execute(select(Job).where(Job.kind == "embed_memory")).scalar_one_or_none()
    assert count(db, MemoryEmbedding) == 0
    # El trabajo desaparece con su propietario o termina descartado; nunca indexa.
    assert job is None or job.result == {
        "outcome": "discarded",
        "reason": "memory_changed_or_deleted",
    }


def test_memory_deleted_while_embedding_is_computed_is_not_indexed(
    client: TestClient, db: Session, providers
) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta la jardinería", "interest")
    memory_id = client.post(
        f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers
    ).json()["id"]
    original_embed = providers.ai.embed

    def embed_then_delete(texts: list[str]) -> list[list[float]]:
        vectors = original_embed(texts)
        client.delete(f"{API}/memories/{memory_id}", headers=headers)  # borrado concurrente
        return vectors

    providers.ai.embed = embed_then_delete

    run_pending()

    assert count(db, MemoryEmbedding) == 0
    assert count(db, ConfirmedMemory) == 0


def test_editing_memory_bumps_version_and_drops_stale_embedding(
    client: TestClient, db: Session
) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta la jardinería", "interest")
    memory_id = client.post(
        f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers
    ).json()["id"]
    run_pending()

    edited = client.patch(
        f"{API}/memories/{memory_id}", headers=headers, json={"content": "Le gusta el huerto"}
    )
    stale_removed = count(db, MemoryEmbedding) == 0
    run_pending()
    db.expire_all()

    assert edited.json()["version"] == 2
    assert stale_removed
    assert db.get(MemoryEmbedding, uuid.UUID(memory_id)).memory_version == 2


def test_outdated_indexing_job_is_discarded(client: TestClient, db: Session) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta la jardinería", "interest")
    memory_id = client.post(
        f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers
    ).json()["id"]
    client.patch(f"{API}/memories/{memory_id}", headers=headers, json={"content": "Otra cosa"})

    run_pending()  # corre el trabajo de la versión 1 y el de la versión 2

    assert db.get(MemoryEmbedding, uuid.UUID(memory_id)).memory_version == 2
    first = db.execute(
        select(Job).where(Job.idempotency_key == f"embed_memory:{memory_id}:1")
    ).scalar_one()
    assert first.result["outcome"] == "discarded"


def test_embedding_failure_is_retried_later(client: TestClient, db: Session, providers) -> None:
    user_id, headers = new_user(client)
    candidate_id = propose(user_id, "Le gusta la jardinería", "interest")
    client.post(f"{API}/memory-candidates/{candidate_id}/confirm", headers=headers)
    providers.ai.failing.add("embed")

    run_pending()

    job = db.execute(select(Job).where(Job.kind == "embed_memory")).scalar_one()
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.last_error == "embedding_unavailable"
    assert count(db, MemoryEmbedding) == 0


def test_memories_and_candidates_are_isolated_between_users(client: TestClient) -> None:
    rosa_id, rosa = new_user(client)
    _, pedro = new_user(client)
    candidate_id = propose(rosa_id, "Le gusta la jardinería", "interest")
    other_candidate = propose(rosa_id, "Prefiere las mañanas")
    memory_id = client.post(f"{API}/memory-candidates/{candidate_id}/confirm", headers=rosa).json()[
        "id"
    ]

    assert client.get(f"{API}/memories", headers=pedro).json()["total"] == 0
    assert client.get(f"{API}/memory-candidates", headers=pedro).json()["total"] == 0
    for method, path in [
        ("delete", f"/memories/{memory_id}"),
        ("patch", f"/memories/{memory_id}"),
        ("post", f"/memory-candidates/{other_candidate}/confirm"),
        ("post", f"/memory-candidates/{other_candidate}/reject"),
        ("patch", f"/memory-candidates/{other_candidate}"),
    ]:
        kwargs = {"json": {"content": "robado"}} if method == "patch" else {}
        response = getattr(client, method)(f"{API}{path}", headers=pedro, **kwargs)
        assert response.status_code == 404, path
    assert client.get(f"{API}/memories", headers=rosa).json()["total"] == 1
