"""Evaluación de recuperación: ¿aparece una fuente esperada entre los primeros resultados?

Es una medida de ingeniería sobre un corpus conocido. No mide eficacia del producto.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, KnowledgeChunk, KnowledgeDocument
from app.modules.rag.retrieval import retrieve


@dataclass
class RagEvalReport:
    total: int = 0
    hits: int = 0
    target: float = 0.9
    misses: list[str] = field(default_factory=list)
    forbidden_found: list[str] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    @property
    def passed(self) -> bool:
        return self.hit_rate >= self.target and not self.forbidden_found


def _slugs(db: Session) -> dict[str, str]:
    """Mapa de identificador recuperado a slug de su fuente."""
    slugs = {str(i): slug for i, slug in db.execute(select(Activity.id, Activity.slug))}
    chunks = db.execute(
        select(KnowledgeChunk.id, KnowledgeDocument.slug).join(
            KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id
        )
    )
    slugs.update({str(chunk_id): slug for chunk_id, slug in chunks})
    return slugs


def run_rag_eval(
    db: Session, owner_id: uuid.UUID, path: Path, territory: str = "CL"
) -> RagEvalReport:
    with path.open(encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)
    slugs = _slugs(db)
    report = RagEvalReport(target=float(spec.get("target_hit_rate", 0.9)))
    for case in spec["queries"]:
        results = retrieve(db, owner_id=owner_id, query=case["query"], territory=territory)
        found = {slugs.get(result.item.id) for result in results}
        if found & set(case.get("forbidden", [])):
            report.forbidden_found.append(case["id"])
        if not case["expected"]:
            continue
        report.total += 1
        if found & set(case["expected"]):
            report.hits += 1
        else:
            report.misses.append(case["id"])
    return report
