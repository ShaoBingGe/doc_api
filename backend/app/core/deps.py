"""
FastAPI dependency injection providers.

get_db           — yields a SQLAlchemy Session per request
get_api_key_auth — authenticates X-API-Key header, returns ApiKey ORM object
get_settings     — re-exported for convenience
get_storage      — returns the configured StorageBackend singleton
get_task_runner  — returns the configured TaskRunner singleton
get_auth_provider — returns the configured AuthProvider singleton
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import AuthenticationError
from app.core.security import verify_api_key

# ── Re-export get_db so routes only need to import from deps ──────────────────
__all__ = [
    "get_db",
    "get_settings",
    "get_api_key_auth",
    "get_storage",
    "get_task_runner",
    "get_auth_provider",
    "DbSession",
    "CurrentSettings",
]

DbSession = Annotated[Session, Depends(get_db)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]


# ── Abstraction layer factories ───────────────────────────────────────────────

@lru_cache
def get_storage():
    """
    Return a StorageBackend instance selected by STORAGE_BACKEND env var.

    local (default) → LocalStorage(UPLOAD_DIR)
    """
    from app.abstractions.storage import LocalStorage, StorageBackend  # noqa: F401

    s = get_settings()
    if s.STORAGE_BACKEND == "local":
        return LocalStorage(s.UPLOAD_DIR)
    raise ValueError(f"Unsupported STORAGE_BACKEND: {s.STORAGE_BACKEND!r}")


@lru_cache
def get_task_runner():
    """
    Return a TaskRunner instance selected by TASK_RUNNER env var.

    sync (default) → SyncRunner
    """
    from app.abstractions.task_runner import SyncRunner, TaskRunner  # noqa: F401

    s = get_settings()
    if s.TASK_RUNNER == "sync":
        return SyncRunner()
    raise ValueError(f"Unsupported TASK_RUNNER: {s.TASK_RUNNER!r}")


@lru_cache
def get_auth_provider():
    """
    Return an AuthProvider instance (currently only SimpleApiKeyAuth).

    Selecting via config is reserved for future OAuth / JWT implementations.
    """
    from app.abstractions.auth import SimpleApiKeyAuth

    return SimpleApiKeyAuth()


async def get_api_key_auth(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: Session = Depends(get_db),
):
    """
    Authenticate a public API request via X-API-Key header.

    Returns the ApiKey ORM object if valid, raises AuthenticationError otherwise.
    Also validates is_active and expiry.
    """
    from datetime import datetime, timezone

    from app.models.api_key import ApiKey

    if not x_api_key:
        raise AuthenticationError("X-API-Key header is required")

    # Fetch all active keys and compare hashes (small table in prototype)
    # Production: index on key_hash for O(1) lookup
    keys = db.query(ApiKey).filter(ApiKey.is_active == True).all()  # noqa: E712
    matched: ApiKey | None = None
    for key in keys:
        if verify_api_key(x_api_key, key.key_hash):
            matched = key
            break

    if matched is None:
        raise AuthenticationError("Invalid or revoked API key")

    if matched.expires_at and matched.expires_at < datetime.now(timezone.utc):
        raise AuthenticationError("API key has expired")

    # Update last_used_at (best-effort, non-blocking)
    try:
        matched.last_used_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()

    return matched
