"""
AuthProvider — API Key 认证抽象层。

实现：
  SimpleApiKeyAuth — SHA-256 校验，复用 api_key_service 的验证逻辑

扩展：添加 JWTAuth、OAuthProvider 等，只需继承 AuthProvider。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import verify_api_key


class AuthProvider(ABC):
    """Abstract interface for authenticating an incoming API key string."""

    @abstractmethod
    def authenticate(self, raw_key: str, db: Session) -> Any:
        """
        Validate *raw_key* against the data store.

        Returns the authenticated identity object on success.
        Raises an appropriate exception on failure.
        """


class SimpleApiKeyAuth(AuthProvider):
    """
    Authenticates requests using SHA-256-hashed API keys stored in the DB.

    Validation steps (mirrors the logic in deps.get_api_key_auth):
      1. Fetch all active ApiKey rows.
      2. Compare hashes with constant-time digest.
      3. Check expiry.
      4. Update last_used_at (best-effort).
    """

    def authenticate(self, raw_key: str, db: Session) -> Any:
        from app.core.exceptions import AuthenticationError
        from app.models.api_key import ApiKey

        if not raw_key:
            raise AuthenticationError("API key must not be empty")

        keys = db.query(ApiKey).filter(ApiKey.is_active == True).all()  # noqa: E712
        matched: ApiKey | None = None
        for key in keys:
            if verify_api_key(raw_key, key.key_hash):
                matched = key
                break

        if matched is None:
            raise AuthenticationError("Invalid or revoked API key")

        if matched.expires_at and matched.expires_at < datetime.now(timezone.utc):
            raise AuthenticationError("API key has expired")

        try:
            matched.last_used_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            db.rollback()

        return matched
