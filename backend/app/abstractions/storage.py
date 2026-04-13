"""
StorageBackend — 文件存储抽象层。

实现：
  LocalStorage — 将文件持久化到本地磁盘 (UPLOAD_DIR)

扩展：添加 S3Storage 等，只需继承 StorageBackend 并实现三个方法。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    """Abstract interface for file storage."""

    @abstractmethod
    def save(self, data: bytes, filename: str) -> str:
        """
        Persist *data* under a unique key derived from *filename*.

        Returns the storage key (opaque string) used to retrieve the file later.
        """

    @abstractmethod
    def load(self, key: str) -> bytes:
        """Return the raw bytes for the given storage *key*."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the file identified by *key*. No-op if it does not exist."""


class LocalStorage(StorageBackend):
    """
    Stores files on the local filesystem under *upload_dir*.

    Key format: ``<uuid4>_<original_filename>``
    This keeps names human-readable while guaranteeing uniqueness.
    """

    def __init__(self, upload_dir: str) -> None:
        self._root = Path(upload_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, filename: str) -> str:
        key = f"{uuid.uuid4()}_{filename}"
        (self._root / key).write_bytes(data)
        return key

    def load(self, key: str) -> bytes:
        path = self._root / key
        if not path.exists():
            raise FileNotFoundError(f"Storage key not found: {key!r}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._root / key
        if path.exists():
            path.unlink()
