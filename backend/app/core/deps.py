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
    "get_current_user",
    "require_roles",
    "DbSession",
    "CurrentSettings",
    "CurrentUser",
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


# ── User auth (JWT bearer)─────────────────────────────────────────────────────

async def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db),
):
    """
    Authenticate a management-UI request via `Authorization: Bearer <jwt>`.

    Returns the User ORM object. Raises AuthenticationError on any failure
    (missing/invalid/expired token, unknown or deactivated user).
    """
    from app.core.security import decode_access_token
    from app.models.user import User

    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Authorization Bearer token is required")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise AuthenticationError(str(exc))

    import uuid as _uuid

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Token missing subject")
    try:
        user_uuid = _uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        raise AuthenticationError("Token subject is malformed")

    user = db.query(User).filter(User.id == user_uuid).first()
    if user is None:
        raise AuthenticationError("User no longer exists")
    if not user.is_active:
        raise AuthenticationError("User account is deactivated")

    return user


def require_roles(*roles: str):
    """
    Dependency factory: gate an endpoint to the given UserRole values.

    Usage:
        @router.get(..., dependencies=[Depends(require_roles(UserRole.super_admin))])
    or to consume the user:
        def handler(user = Depends(require_roles(UserRole.super_admin))): ...

    Accepts UserRole members or their string values.
    """
    from app.core.exceptions import AuthorizationError

    allowed = {r.value if hasattr(r, "value") else str(r) for r in roles}

    async def _guard(user=Depends(get_current_user)):
        if user.role not in allowed:
            raise AuthorizationError(
                f"Requires one of roles: {sorted(allowed)} (you are '{user.role}')"
            )
        return user

    return _guard


# Convenience annotated type for handlers that just need the authed user.
from typing import Any as _Any  # noqa: E402

CurrentUser = Annotated[_Any, Depends(get_current_user)]
