"""Almacenamiento privado de archivos temporales."""

from typing import Protocol


class StorageError(Exception):
    pass


class Storage(Protocol):
    name: str

    def put(self, path: str, data: bytes, content_type: str) -> None: ...

    def get(self, path: str) -> bytes: ...

    def delete(self, paths: list[str]) -> None: ...
