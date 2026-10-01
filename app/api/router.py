"""Ensamblado de las rutas de la versión 1."""

from fastapi import APIRouter

from app.api import health

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
