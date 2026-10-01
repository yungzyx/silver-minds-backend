"""Extensiones y configuración de búsqueda textual en español sin acentos.

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    # "jardinería" y "jardineria" deben encontrar lo mismo.
    op.execute("CREATE TEXT SEARCH CONFIGURATION es_unaccent (COPY = spanish)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION es_unaccent "
        "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem"
    )


def downgrade() -> None:
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS es_unaccent")
