"""Aplicación FastAPI."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.errors import register_error_handlers

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
WEB_SURFACES = ("device", "family", "shared")

DESCRIPTION = """
Backend del MVP de Silver Minds.

El agente conversa con una persona mayor autovalente, recuerda preferencias
**confirmadas**, propone actividades y facilita invitaciones **aprobadas**.
La efectividad sobre soledad o comprensión no está demostrada.

Este servicio no es un servicio de emergencia ni entrega atención clínica.
"""


def _page_security_headers(request: Request) -> dict[str, str]:
    """Cabeceras para las páginas del dispositivo y del panel familiar."""
    host = request.headers.get("host", "")
    policy = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' https://fonts.googleapis.com",
            "font-src https://fonts.gstatic.com",
            "img-src 'self' blob: data:",
            "media-src 'self' blob:",
            f"connect-src 'self' ws://{host} wss://{host}",
            "frame-ancestors 'none'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )
    return {
        "Content-Security-Policy": policy,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        # Cámara y micrófono solo para la propia página; el panel familiar no los usa.
        "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
        "Cache-Control": "no-store",
    }


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Silver Minds API", version="0.1.0", description=DESCRIPTION)
    register_error_handlers(app)
    app.include_router(api_router)

    @app.middleware("http")
    async def secure_pages(request: Request, call_next) -> Response:  # noqa: ANN001
        response = await call_next(request)
        if request.url.path.split("/")[1] in WEB_SURFACES:
            response.headers.update(_page_security_headers(request))
        return response

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    for surface in WEB_SURFACES:
        app.mount(f"/{surface}", StaticFiles(directory=WEB_DIR / surface, html=True), name=surface)
    return app


app = create_app()
