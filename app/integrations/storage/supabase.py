"""Supabase Storage con un bucket privado.

Rutas verificadas contra la documentación el 1 de octubre de 2026. **No se ejecutó
contra un proyecto real**: faltan credenciales.
"""

import httpx

from app.integrations.storage.base import StorageError

USER_AGENT = "silver-minds-backend/0.1"


class SupabaseStorage:
    name = "supabase"

    def __init__(
        self,
        url: str,
        service_role_key: str,
        bucket: str,
        *,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._bucket = bucket
        self._client = httpx.Client(
            base_url=f"{url.rstrip('/')}/storage/v1",
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {service_role_key}",
                "apikey": service_role_key,
                "User-Agent": USER_AGENT,
            },
        )

    def _check(self, response: httpx.Response, action: str) -> httpx.Response:
        if response.status_code >= 400:
            raise StorageError(f"Supabase Storage rechazó {action} ({response.status_code})")
        return response

    def put(self, path: str, data: bytes, content_type: str) -> None:
        try:
            response = self._client.post(
                f"/object/{self._bucket}/{path}",
                content=data,
                headers={"Content-Type": content_type, "x-upsert": "true"},
            )
        except httpx.HTTPError as exc:
            raise StorageError("No se pudo subir el archivo") from exc
        self._check(response, "la subida")

    def get(self, path: str) -> bytes:
        try:
            response = self._client.get(f"/object/authenticated/{self._bucket}/{path}")
        except httpx.HTTPError as exc:
            raise StorageError("No se pudo descargar el archivo") from exc
        return self._check(response, "la descarga").content

    def delete(self, paths: list[str]) -> None:
        if not paths:
            return
        try:
            response = self._client.request(
                "DELETE", f"/object/{self._bucket}", json={"prefixes": paths}
            )
        except httpx.HTTPError as exc:
            raise StorageError("No se pudo borrar el archivo") from exc
        self._check(response, "el borrado")
