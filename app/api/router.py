"""Ensamblado de las rutas de la versión 1."""

from fastapi import APIRouter

from app.api import health
from app.modules.actions import router as actions
from app.modules.contacts import router as contacts
from app.modules.conversations import router as conversations
from app.modules.memory import router as memory
from app.modules.profiles import router as profiles
from app.modules.safety import router as safety

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(profiles.router)
api_router.include_router(contacts.router)
api_router.include_router(memory.router)
api_router.include_router(safety.router)
api_router.include_router(conversations.router)
api_router.include_router(actions.router)
