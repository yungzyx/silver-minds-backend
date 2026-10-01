"""Salud del servicio."""

from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db

router = APIRouter(tags=["salud"])


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    database: bool
    pgvector: bool
    ai_provider: str
    email_provider: str
    storage_provider: str


@router.get("/health", response_model=Health, summary="Proceso vivo")
def health() -> Health:
    return Health(status="ok", version="0.1.0")


@router.get(
    "/health/ready",
    response_model=Readiness,
    summary="Dependencias listas",
    description="Indica además si cada integración es real o simulada (`fake`, `local`).",
)
def ready(
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Readiness:
    database = pgvector = False
    try:
        db.execute(text("SELECT 1"))
        database = True
        pgvector = (
            db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
            is not None
        )
    except SQLAlchemyError:
        db.rollback()
    is_ready = database and pgvector
    if not is_ready:
        response.status_code = 503
    return Readiness(
        status="ready" if is_ready else "degraded",
        database=database,
        pgvector=pgvector,
        ai_provider=settings.ai_provider,
        email_provider=settings.email_provider,
        storage_provider=settings.storage_provider,
    )
