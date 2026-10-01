"""Aplicación FastAPI."""

import logging

from fastapi import FastAPI

from app.api.router import api_router
from app.core.errors import register_error_handlers

DESCRIPTION = """
Backend del MVP de Silver Minds.

El agente conversa con una persona mayor autovalente, recuerda preferencias
**confirmadas**, propone actividades y facilita invitaciones **aprobadas**.
La efectividad sobre soledad o comprensión no está demostrada.

Este servicio no es un servicio de emergencia ni entrega atención clínica.
"""


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Silver Minds API", version="0.1.0", description=DESCRIPTION)
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
