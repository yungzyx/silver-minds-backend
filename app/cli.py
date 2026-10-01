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


def demo_setup(args: argparse.Namespace) -> int:
    """Prepara la demostración: persona ficticia, dispositivo y acceso familiar."""
    from app.modules.devices.demo import setup_demo

    with session_scope() as db:
        links = setup_demo(db, device_name=args.device_name)
    sys.stdout.write(
        "Demostración lista (datos ficticios).\n\n"
        f"Dispositivo de Rosa:\n  {links.device_url}\n\n"
        f"Panel familiar de Camila:\n  {links.family_url}\n\n"
        "Los enlaces contienen tokens: no los compartas fuera de la demostración.\n"
    )
    return 0


def eval_rag(_args: argparse.Namespace) -> int:
    """Evaluación de recuperación sobre el corpus ingerido."""
    from app.modules.devices.demo import DEMO_OWNER_ID
    from app.modules.profiles import repository as profiles
    from app.modules.rag.evaluation import run_rag_eval

    with session_scope() as db:
        profiles.get_or_create_profile(db, DEMO_OWNER_ID, None)
        report = run_rag_eval(db, DEMO_OWNER_ID, Path("evals/rag_queries.yaml"))
    sys.stdout.write(
        f"Fuente esperada entre los 5 primeros: {report.hits}/{report.total} "
        f"({report.hit_rate:.0%}; objetivo {report.target:.0%})\n"
        f"Sin fuente esperada: {', '.join(report.misses) or 'ninguna'}\n"
        f"Contenido excluido recuperado: {', '.join(report.forbidden_found) or 'ninguno'}\n"
        "Es una medida de ingeniería, no de eficacia del producto.\n"
    )
    return 0 if report.passed else 1


def eval_safety(args: argparse.Namespace) -> int:
    """Escenarios sintéticos de seguridad, con señales simuladas o con el proveedor real."""
    from app.modules.safety.evaluation import run_safety_eval

    if args.live and get_settings().ai_provider != "openai":
        sys.stderr.write("--live requiere AI_PROVIDER=openai y credenciales.\n")
        return 2
    report = run_safety_eval(Path("evals/safety_scenarios.yaml"), live=args.live)
    sys.stdout.write(
        f"Modo: {'proveedor real' if args.live else 'señales simuladas'}\n"
        f"Escenarios con la ruta esperada: {report.passed}/{report.total}\n"
        f"Urgentes detectados: {report.urgent_detected}/{report.urgent_total}\n"
        f"Falsos negativos (menos protector): {report.false_negatives or 'ninguno'}\n"
        f"Falsos positivos (más protector): {report.false_positives or 'ninguno'}\n"
        f"Revisión profesional: {report.professional_review}\n"
        "Este conjunto no garantiza detección universal.\n"
    )
    return 0 if not report.false_negatives and report.all_urgent_detected else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Administración de Silver Minds")
    commands = parser.add_subparsers(dest="command", required=True)

    token = commands.add_parser("dev-token", help="JWT local para desarrollo")
    token.add_argument("--email", default="demo@example.com")
    token.add_argument("--sub", help="UUID de la persona usuaria")
    token.add_argument("--hours", type=int, default=12)
    token.set_defaults(handler=dev_token)

    demo = commands.add_parser("demo-setup", help="Datos ficticios y enlaces de la demostración")
    demo.add_argument(
        "--device-name", default="Silvia", help="Nombre con que se llama al dispositivo"
    )
    demo.set_defaults(handler=demo_setup)

    rag = commands.add_parser("eval-rag", help="Evaluación de recuperación")
    rag.set_defaults(handler=eval_rag)

    safety = commands.add_parser("eval-safety", help="Escenarios sintéticos de seguridad")
    safety.add_argument("--live", action="store_true", help="Usa el proveedor real")
    safety.set_defaults(handler=eval_safety)

    ingest_parser = commands.add_parser("ingest", help="Ingesta de conocimiento revisado")
    ingest_parser.add_argument("manifest", help="Ruta al manifiesto YAML")
    ingest_parser.set_defaults(handler=ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
