import uuid
from datetime import date
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.core.text import estimate_tokens
from app.integrations import registry
from app.integrations.ai.base import MemoryDraft
from app.models import (
    Activity,
    IndexState,
    KnowledgeChunk,
    KnowledgeDocument,
    MemoryEmbedding,
    RetrievalRun,
)
from app.modules.memory import service as memory
from app.modules.rag.chunking import MAX_TOKENS, MIN_TOKENS, chunk_text
from app.modules.rag.evaluation import run_rag_eval
from app.modules.rag.ingest import ingest_manifest
from app.modules.rag.retrieval import retrieve
from app.worker.main import run_pending
from tests.helpers import new_user

MANIFEST = Path("data/knowledge/manifest.yaml")


def search(owner_id: uuid.UUID, query: str, **kwargs: object) -> list:
    with session_scope() as db:
        return retrieve(db, owner_id=owner_id, query=query, territory="CL", **kwargs)


def titles(results: list) -> list[str]:
    return [result.item.title for result in results]


def remember(owner_id: uuid.UUID, content: str, category: str = "interest") -> str:
    with session_scope() as db:
        [candidate] = memory.propose_candidates(
            db, owner_id, None, [MemoryDraft(category=category, content=content)]
        )
        memory_id = str(memory.confirm_candidate(db, owner_id, candidate.id).id)
    run_pending()
    return memory_id


def write_manifest(tmp_path: Path, documents: list[dict], texts: dict[str, str]) -> Path:
    for name, text in texts.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    base = {
        "collection": "guide",
        "source": "Prueba",
        "responsible": "Equipo",
        "reviewed_at": "2026-10-01",
        "version": "1",
        "territory": "CL",
        "approval_status": "approved",
        "valid_from": "2026-01-01",
    }
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump({"documents": [{**base, **doc} for doc in documents]}), encoding="utf-8"
    )
    return manifest


def test_chunks_stay_within_the_target_size() -> None:
    paragraph = "La conversación con la familia mejora cuando cada persona escucha. " * 12
    text = "\n\n".join([paragraph] * 12)

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(estimate_tokens(chunk) <= MAX_TOKENS + MIN_TOKENS // 2 for chunk in chunks)
    assert all(estimate_tokens(chunk) >= MIN_TOKENS for chunk in chunks[:-1])


def test_oversized_paragraph_is_split_by_sentences() -> None:
    text = "Esta es una oración bastante larga sobre escuchar con atención. " * 80

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(estimate_tokens(chunk) <= MAX_TOKENS for chunk in chunks[:-1])
    assert estimate_tokens(chunks[-1]) <= MAX_TOKENS + MIN_TOKENS // 2  # resto unido al último


def test_ingest_registers_metadata_and_publishes_an_index_version(
    knowledge: None, db: Session
) -> None:
    document = db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.slug == "guia-escucha-activa")
    ).scalar_one()

    assert document.source and document.responsible and document.reviewed_at
    assert document.version == "1" and document.territory == "CL"
    assert document.approval_status == "approved" and document.valid_until
    assert db.get(IndexState, 1).version == 1
    assert db.execute(select(func.count()).select_from(Activity)).scalar_one() == 15


def test_unapproved_documents_are_registered_but_not_indexed(knowledge: None, db: Session) -> None:
    draft = db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.slug == "borrador-rutinas-diarias")
    ).scalar_one()
    chunks = db.execute(
        select(func.count())
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == draft.id)
    ).scalar_one()

    assert draft.approval_status == "draft"
    assert chunks == 0


def test_reingesting_is_idempotent(knowledge: None, db: Session) -> None:
    before = db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar_one()

    with session_scope() as session:
        report = ingest_manifest(session, MANIFEST)

    after = db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar_one()
    assert before == after
    assert report.documents_indexed == 0 and report.activities_upserted == 0
    assert report.index_version == 1  # sin cambios no se publica otra versión


def test_duplicate_fragments_are_embedded_once(tmp_path: Path, db: Session) -> None:
    text = "Escuchar con atención ayuda a que la otra persona se sienta comprendida."
    manifest = write_manifest(
        tmp_path,
        [
            {"slug": "doc-a", "title": "A", "path": "a.md"},
            {"slug": "doc-b", "title": "B", "path": "b.md"},
        ],
        {"a.md": text, "b.md": text.upper()},  # mismo contenido tras normalizar
    )

    with session_scope() as session:
        report = ingest_manifest(session, manifest)

    assert report.chunks_created == 1 and report.chunks_deduplicated == 1


def test_changed_content_without_new_version_is_an_error(tmp_path: Path) -> None:
    document = [{"slug": "doc-a", "title": "A", "path": "a.md"}]
    with session_scope() as session:
        ingest_manifest(session, write_manifest(tmp_path, document, {"a.md": "Texto original."}))

    with session_scope() as session:
        report = ingest_manifest(
            session, write_manifest(tmp_path, document, {"a.md": "Texto modificado."})
        )

    assert report.errors and "versión" in report.errors[0]


def test_new_version_supersedes_the_previous_one(tmp_path: Path, client: TestClient) -> None:
    user_id, _ = new_user(client)
    first = [{"slug": "doc-a", "title": "Guía", "path": "a.md"}]
    second = [{"slug": "doc-a", "title": "Guía", "path": "a.md", "version": "2"}]
    with session_scope() as session:
        ingest_manifest(
            session, write_manifest(tmp_path, first, {"a.md": "Consejo sobre ajedrez."})
        )
    with session_scope() as session:
        ingest_manifest(
            session, write_manifest(tmp_path, second, {"a.md": "Consejo sobre ajedrez nuevo."})
        )

    results = search(user_id, "ajedrez")

    assert [result.item.version for result in results] == ["2"]


def test_retrieval_finds_activity_with_details_from_the_record(
    knowledge: None, client: TestClient
) -> None:
    user_id, _ = new_user(client)

    results = search(user_id, "Me gustaría cultivar plantas en un huerto")

    top = results[0].item
    assert top.type == "activity" and top.title == "Taller de huerto comunitario"
    assert "Martes de 10:00 a 11:30" in top.text and "Gratuito" in top.text


def test_retrieval_ignores_accents(knowledge: None, client: TestClient) -> None:
    user_id, _ = new_user(client)

    assert "Taller de huerto comunitario" in titles(search(user_id, "jardineria y hortalizas"))


def test_unapproved_and_expired_content_is_never_retrieved(
    knowledge: None, client: TestClient
) -> None:
    user_id, _ = new_user(client)

    museum = titles(search(user_id, "paseo al museo con visita guiada"))
    fair = titles(search(user_id, "feria de trueque de plantas y esquejes"))
    summer = titles(search(user_id, "piscina y paseo a la playa en verano"))

    assert "Paseo al museo" not in museum
    assert "Feria de trueque de plantas" not in fair
    assert "Agenda de verano 2025" not in summer


def test_unapproved_chunks_are_filtered_even_if_they_exist(
    knowledge: None, client: TestClient, db: Session
) -> None:
    """Defensa en la consulta: aunque un fragmento exista, su documento debe estar aprobado."""
    user_id, _ = new_user(client)
    draft = db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.slug == "borrador-rutinas-diarias")
    ).scalar_one()
    text = "Recomendación sobre siestas y horarios de sueño sin revisar."
    db.add(
        KnowledgeChunk(
            document_id=draft.id,
            chunk_index=0,
            content=text,
            token_count=20,
            content_hash="x" * 64,
            embedding=registry.get_ai().embed([text])[0],
        )
    )
    db.commit()

    assert "Rutinas diarias (borrador)" not in titles(
        search(user_id, "siestas y horarios de sueño")
    )


def test_content_expires_on_its_validity_date(knowledge: None, client: TestClient) -> None:
    user_id, _ = new_user(client)
    query = "cultivar plantas en un huerto"

    during = titles(search(user_id, query, today=date(2027, 3, 31)))
    after = titles(search(user_id, query, today=date(2027, 4, 1)))
    before = titles(search(user_id, query, today=date(2026, 9, 30)))

    assert "Taller de huerto comunitario" in during
    assert "Taller de huerto comunitario" not in after
    assert "Taller de huerto comunitario" not in before


def test_content_from_another_territory_is_not_retrieved(
    knowledge: None, client: TestClient
) -> None:
    user_id, _ = new_user(client)

    with session_scope() as db:
        results = retrieve(db, owner_id=user_id, query="huerto y plantas", territory="AR")

    assert results == []


def test_owner_retrieves_their_own_confirmed_memory(knowledge: None, client: TestClient) -> None:
    user_id, _ = new_user(client)
    memory_id = remember(user_id, "Le gusta la jardinería y cuidar sus rosas")

    results = search(user_id, "¿qué puedo hacer con mis rosas?")

    assert memory_id in [result.item.id for result in results if result.item.type == "memory"]


def test_memories_of_other_users_are_never_retrieved(knowledge: None, client: TestClient) -> None:
    rosa, _ = new_user(client)
    pedro, _ = new_user(client)
    remember(rosa, "Le gusta la jardinería y cuidar sus rosas")
    remember(pedro, "Le gusta el ajedrez")

    results = search(pedro, "jardinería rosas ajedrez")

    memories = [result.item.text for result in results if result.item.type == "memory"]
    assert memories == ["Le gusta el ajedrez"]


def test_deleted_memory_is_not_retrieved(knowledge: None, client: TestClient) -> None:
    user_id, headers = new_user(client)
    memory_id = remember(user_id, "Le gusta el ajedrez")

    client.delete(f"/api/v1/memories/{memory_id}", headers=headers)

    assert [r for r in search(user_id, "ajedrez") if r.item.type == "memory"] == []


def test_stale_embedding_of_an_edited_memory_is_ignored(
    knowledge: None, client: TestClient, db: Session
) -> None:
    user_id, _ = new_user(client)
    memory_id = remember(user_id, "Le gusta el ajedrez")
    embedding = db.get(MemoryEmbedding, uuid.UUID(memory_id))
    embedding.memory_version = 0  # embedding de una versión que ya no existe
    db.commit()

    with session_scope() as session:
        # Sin términos de texto útiles solo actúa la búsqueda vectorial.
        results = retrieve(session, owner_id=user_id, query="ajedrez", territory="ZZ")

    vector_only = [r for r in results if r.item.type == "memory"]
    assert len(vector_only) == 1  # llega por texto, no por el embedding obsoleto
    assert vector_only[0].score < 2 / 61  # una sola lista aportó al ranking


def test_retrieval_respects_item_and_token_limits(knowledge: None, client: TestClient) -> None:
    user_id, _ = new_user(client)

    results = search(user_id, "actividades conversación familia taller gratuito plantas lectura")

    assert 0 < len(results) <= 5
    assert sum(result.tokens for result in results) <= 2500


def test_retrieval_run_records_ids_and_versions_without_text(
    knowledge: None, client: TestClient, db: Session
) -> None:
    user_id, _ = new_user(client)

    results = search(user_id, "cultivar plantas en un huerto")

    run = db.execute(select(RetrievalRun)).scalar_one()
    assert run.index_version == 1
    assert [item["id"] for item in run.items] == [result.item.id for result in results]
    assert all(set(item) == {"type", "id", "version"} for item in run.items)


def test_retrieval_falls_back_to_text_when_embeddings_fail(
    knowledge: None, client: TestClient, providers
) -> None:
    user_id, _ = new_user(client)
    providers.ai.failing.add("embed")

    assert "Taller de huerto comunitario" in titles(search(user_id, "huerto comunitario"))


def test_unrelated_query_retrieves_nothing(knowledge: None, client: TestClient) -> None:
    user_id, _ = new_user(client)

    assert search(user_id, "xilófono cuántico zzz") == []


def test_rag_evaluation_meets_the_engineering_target(knowledge: None, client: TestClient) -> None:
    user_id, _ = new_user(client)

    with session_scope() as db:
        report = run_rag_eval(db, user_id, Path("evals/rag_queries.yaml"))

    assert report.total >= 30
    assert report.forbidden_found == []
    assert report.hit_rate >= report.target, f"Sin fuente esperada: {report.misses}"
