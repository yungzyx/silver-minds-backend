"""Adaptador de Resend (https://resend.com/docs/api-reference/emails/send-email).

Verificado contra la documentación el 1 de octubre de 2026:
``Authorization: Bearer``, ``User-Agent`` obligatorio e ``Idempotency-Key`` (24 horas).
"""

import httpx

from app.integrations.email.base import (
    EmailAmbiguousError,
    EmailMessage,
    EmailRejectedError,
    EmailTransientError,
)

RESEND_URL = "https://api.resend.com/emails"
USER_AGENT = "silver-minds-backend/0.1"
# Con esta respuesta el proveedor pide repetir la misma petición más tarde.
RETRY_LATER_ERROR = "concurrent_idempotent_requests"


class ResendEmailSender:
    name = "resend"

    def __init__(
        self,
        api_key: str,
        sender: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._sender = sender
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        )

    def send(self, message: EmailMessage) -> str:
        try:
            response = self._client.post(
                RESEND_URL,
                headers={"Idempotency-Key": message.idempotency_key},
                json={
                    "from": self._sender,
                    "to": [message.to],
                    "subject": message.subject,
                    "text": message.text,
                },
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # No se llegó a establecer la conexión: el correo no salió.
            raise EmailTransientError("No se pudo conectar con Resend") from exc
        except httpx.HTTPError as exc:
            raise EmailAmbiguousError("Resend no respondió; se desconoce si envió") from exc
        return self._interpret(response)

    @staticmethod
    def _interpret(response: httpx.Response) -> str:
        status = response.status_code
        if 200 <= status < 300:
            try:
                return str(response.json()["id"])
            except (ValueError, KeyError) as exc:
                raise EmailAmbiguousError("Respuesta de Resend sin identificador") from exc
        if status == 429 or (status == 409 and RETRY_LATER_ERROR in response.text):
            raise EmailTransientError(f"Resend pidió reintentar ({status})")
        if 400 <= status < 500:
            raise EmailRejectedError(f"Resend rechazó el envío ({status})")
        raise EmailAmbiguousError(f"Error de Resend ({status})")
