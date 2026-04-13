"""
abstractions — Backend interface contracts and default implementations.

公开三个 ABC 及其默认实现，供 deps.py 工厂函数使用。
"""

from app.abstractions.auth import AuthProvider, SimpleApiKeyAuth
from app.abstractions.storage import LocalStorage, StorageBackend
from app.abstractions.task_runner import SyncRunner, TaskRunner

__all__ = [
    "StorageBackend",
    "LocalStorage",
    "TaskRunner",
    "SyncRunner",
    "AuthProvider",
    "SimpleApiKeyAuth",
]
