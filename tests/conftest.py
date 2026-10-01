"""Fixtures compartidos. Las pruebas usan PostgreSQL real y proveedores simulados."""

import os

# La configuración se fija antes de importar la aplicación.
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/silver_minds_test"
)
os.environ["SUPABASE_JWT_SECRET"] = "test-secret-solo-para-pruebas-0123456789abcdef"
os.environ.pop("SUPABASE_JWKS_URL", None)
os.environ["AI_PROVIDER"] = "fake"
os.environ["EMAIL_PROVIDER"] = "fake"
os.environ["STORAGE_PROVIDER"] = "local"

from collections.abc import Iterator  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import get_engine, get_session_factory  # noqa: E402
from app.integrations import registry  # noqa: E402
from app.integrations.ai.fake import FakeAIProvider  # noqa: E402
from app.integrations.email.fake import FakeEmailSender  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    """Recrea el esquema y aplica todas las migraciones: también prueba las migraciones."""
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if tables:
        with get_engine().begin() as connection:
            connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))  # noqa: S608


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def providers() -> Iterator[SimpleNamespace]:
    """Proveedores simulados nuevos en cada prueba, accesibles para inspeccionarlos."""
    adapters = SimpleNamespace(ai=FakeAIProvider(), email=FakeEmailSender())
    registry.override(ai=adapters.ai, email=adapters.email)
    yield adapters
    registry.clear_overrides()


@pytest.fixture
def knowledge() -> None:
    """Corpus de demostración ingerido con embeddings simulados."""
    from pathlib import Path

    from app.core.db import session_scope
    from app.modules.rag.ingest import ingest_manifest

    with session_scope() as session:
        ingest_manifest(session, Path("data/knowledge/manifest.yaml"))
