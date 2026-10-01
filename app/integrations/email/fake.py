"""Correo simulado: guarda los mensajes en memoria y, opcionalmente, en una carpeta local.

No envía nada. Respeta la clave de idempotencia como lo haría un proveedor real.
"""

import json
import re
from pathlib import Path

from app.integrations.email.base import EmailMessage


class FakeEmailSender:
    name = "fake"

    def __init__(self, outbox_dir: str | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self.fail_next_with: Exception | None = None
        self._by_key: dict[str, str] = {}
        self._outbox = Path(outbox_dir) if outbox_dir else None

    def send(self, message: EmailMessage) -> str:
        if self.fail_next_with is not None:
            error, self.fail_next_with = self.fail_next_with, None
            raise error
        if message.idempotency_key in self._by_key:
            return self._by_key[message.idempotency_key]
        provider_id = f"fake-{len(self.sent) + 1}"
        self._by_key[message.idempotency_key] = provider_id
        self.sent.append(message)
        self._write(provider_id, message)
        return provider_id

    def _write(self, provider_id: str, message: EmailMessage) -> None:
        if self._outbox is None:
            return
        self._outbox.mkdir(parents=True, exist_ok=True)
        safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", message.idempotency_key)
        payload = {
            "id": provider_id,
            "to": message.to,
            "subject": message.subject,
            "text": message.text,
            "simulated": True,
        }
        (self._outbox / f"{safe_key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
