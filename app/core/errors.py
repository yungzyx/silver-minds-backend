"""Errores de dominio y su traducción a respuestas HTTP con un sobre uniforme."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class UnauthenticatedError(AppError):
    status_code = 401
    code = "unauthenticated"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ActionsPausedError(ConflictError):
    code = "actions_paused"


class TokenExpiredError(AppError):
    status_code = 410
    code = "token_expired"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class InvalidInputError(AppError):
    status_code = 422
    code = "validation_error"


class QuotaExceededError(AppError):
    status_code = 429
    code = "quota_exceeded"


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
        return _envelope(422, "validation_error", f"Entrada inválida: {', '.join(fields)}")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _envelope(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # El detalle queda en el log del servidor; el cliente no recibe datos internos.
        # Sin el mensaje ni la ruta: podrían incluir parámetros de SQL, correos o tokens.
        route = getattr(request.scope.get("route"), "path", "ruta desconocida")
        logger.error("Error no controlado en %s: %s", route, type(exc).__name__)
        return _envelope(500, "internal_error", "Ocurrió un error inesperado.")
