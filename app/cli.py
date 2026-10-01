"""Comandos administrativos: ``python -m app.cli <comando>``."""

import argparse
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import jwt

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import session_scope


def dev_token(args: argparse.Namespace) -> int:
    """Firma un JWT local con el secreto HS256. Solo para desarrollo."""
    settings = get_settings()
    if settings.environment == "production" or not settings.supabase_jwt_secret:
        sys.stderr.write("dev-token requiere SUPABASE_JWT_SECRET y no funciona en producción.\n")
        return 1
    now = utcnow()
    claims = {
        "sub": args.sub or str(uuid.uuid4()),
        "email": args.email,
        "aud": settings.supabase_jwt_audience,
        "role": "authenticated",
        "iat": now,
        "exp": now + timedelta(hours=args.hours),
    }
    if settings.supabase_url:
        claims["iss"] = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    secret = settings.supabase_jwt_secret.get_secret_value()
    sys.stdout.write(jwt.encode(claims, secret, algorithm="HS256") + "\n")
    return 0


def ingest(args: argparse.Namespace) -> int:
    """Ingesta de conocimiento revisado desde un manifiesto."""
    from app.modules.rag.ingest import ingest_manifest

    with session_scope() as db:
        report = ingest_manifest(db, Path(args.manifest))
    sys.stdout.write(
        f"Documentos indexados: {report.documents_indexed} · sin cambios: "
        f"{report.documents_unchanged} · no aprobados: {report.documents_not_approved}\n"
        f"Fragmentos creados: {report.chunks_created} · duplicados omitidos: "
        f"{report.chunks_deduplicated}\n"
        f"Actividades actualizadas: {report.activities_upserted} · sin cambios: "
        f"{report.activities_unchanged}\n"
        f"Versión del índice: {report.index_version}\n"
    )
    for error in report.errors:
        sys.stderr.write(f"ERROR: {error}\n")
    return 1 if report.errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Administración de Silver Minds")
    commands = parser.add_subparsers(dest="command", required=True)

    token = commands.add_parser("dev-token", help="JWT local para desarrollo")
    token.add_argument("--email", default="demo@example.com")
    token.add_argument("--sub", help="UUID de la persona usuaria")
    token.add_argument("--hours", type=int, default=12)
    token.set_defaults(handler=dev_token)

    ingest_parser = commands.add_parser("ingest", help="Ingesta de conocimiento revisado")
    ingest_parser.add_argument("manifest", help="Ruta al manifiesto YAML")
    ingest_parser.set_defaults(handler=ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
