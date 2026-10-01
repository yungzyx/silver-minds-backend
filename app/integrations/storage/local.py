"""Almacenamiento en disco local, para desarrollo y pruebas."""

from pathlib import Path

from app.integrations.storage.base import StorageError


class LocalStorage:
    name = "local"

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def _resolve(self, path: str) -> Path:
        target = (self._root / path).resolve()
        if not target.is_relative_to(self._root):
            raise StorageError("Ruta fuera del almacenamiento")
        return target

    def put(self, path: str, data: bytes, content_type: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get(self, path: str) -> bytes:
        try:
            return self._resolve(path).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("El archivo no existe") from exc

    def delete(self, paths: list[str]) -> None:
        for path in paths:
            self._resolve(path).unlink(missing_ok=True)
