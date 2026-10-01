from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import main
from app.core.db import session_scope
from app.integrations import registry
from app.integrations.ai.check import run_checks
from app.integrations.ai.fake import FakeAIProvider
from app.models import IndexState, KnowledgeChunk, MemoryEmbedding
from app.modules.rag.reindex import reindex_all
from app.modules.rag.retrieval import retrieve
from tests.helpers import API, new_user


class OtherModel(FakeAIProvider):
    """Simula el cambio a otro modelo de embeddings: mismos textos, otro espacio vectorial."""

    embedding_model = "otro:modelo-v2"

    def _embed_one(self, text: str) -> list[float]:
        return list(reversed(super()._embed_one(text)))


def search(owner_id, query: str) -> list:
    with session_scope() as db:
        return retrieve(db, owner_id=owner_id, query=query, territory="CL")


def test_index_remembers_the_model_that_built_it(knowledge: None, db: Session) -> None:
    assert db.get(IndexState, 1).embedding_model == "fake:hash-v1"


def test_vectors_from_another_model_are_not_compared(knowledge: None, client: TestClient) -> None:
    """Tras cambiar de proveedor sin reindexar, la búsqueda vectorial se omite."""
    user_id, _ = new_user(client)
    registry.override(ai=OtherModel())

    results = search(user_id, "cultivar plantas en un huerto")

    titles = [result.item.title for result in results]
    assert "Taller de huerto comunitario" in titles  # llega por texto
    assert all(result.score <= 1 / 61 for result in results)  # una sola lista: sin vectores


def test_reindex_rebuilds_every_embedding_with_the_current_model(
    knowledge: None, client: TestClient, db: Session
) -> None:
    user_id, headers = new_user(client)
    client.post(f"{API}/preferences", headers=headers, json={"category": "interest", "value": "x"})
    with session_scope() as session:
        from app.integrations.ai.base import MemoryDraft
        from app.modules.memory import service as memory

        [candidate] = memory.propose_candidates(
            session,
            user_id,
            None,
            [MemoryDraft(category="interest", content="Le gusta el ajedrez")],
        )
        memory.confirm_candidate(session, user_id, candidate.id)
    before = list(db.execute(select(KnowledgeChunk.embedding).limit(1)).scalar_one())
    other = OtherModel()
    registry.override(ai=other)

    with session_scope() as session:
        report = reindex_all(session)

    db.expire_all()
    after = list(db.execute(select(KnowledgeChunk.embedding).limit(1)).scalar_one())
    state = db.get(IndexState, 1)
    assert report.chunks == 11 and report.activities == 15 and report.memories == 1
    assert after != before
    assert state.embedding_model == "otro:modelo-v2" and state.version == 2
    assert db.execute(select(MemoryEmbedding.memory_version)).scalar_one() == 1
    # Con el índice reconstruido, la búsqueda vectorial vuelve a aportar.
    results = search(user_id, "cultivar plantas en un huerto")
    assert results[0].score > 1 / 61


def test_failed_reindex_leaves_the_index_untouched(knowledge: None, db: Session, providers) -> None:
    before = list(db.execute(select(KnowledgeChunk.embedding).limit(1)).scalar_one())
    providers.ai.failing.add("embed")

    try:
        with session_scope() as session:
            reindex_all(session)
    except Exception as exc:  # noqa: BLE001
        failed = type(exc).__name__

    db.expire_all()
    assert failed == "AIError"
    assert list(db.execute(select(KnowledgeChunk.embedding).limit(1)).scalar_one()) == before
    assert db.get(IndexState, 1).version == 1


def test_provider_check_reports_each_capability(providers) -> None:
    ok = run_checks()
    providers.ai.failing.update({"generate", "transcribe"})
    broken = {result.capability: result.ok for result in run_checks()}

    assert [result.ok for result in ok] == [True] * 5
    assert broken == {
        "embeddings": True,
        "moderación": True,
        "clasificador de seguridad": True,
        "generación estructurada": False,
        "voz y transcripción": False,
    }


def test_cli_check_and_reindex_commands(knowledge: None, capsys, providers) -> None:
    assert main(["check-ai"]) == 0
    assert main(["reindex"]) == 0
    providers.ai.failing.add("embed")
    assert main(["check-ai"]) == 1

    output = capsys.readouterr().out
    assert "Proveedor: fake" in output and "Reindexado con fake:hash-v1" in output
    assert "FALLA embeddings" in output
