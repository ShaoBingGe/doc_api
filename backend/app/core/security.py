"""
Security utilities: API Key generation, hashing, and verification.

密钥格式：sk-<Base62(32 random bytes)>
存储：只保存 SHA-256 哈希，明文仅在创建时返回一次。
"""

from __future__ import annotations

import hashlib
import secrets
import string

from app.core.config import get_settings

settings = get_settings()

# Base62 字母表（URL-safe，无歧义字符）
_BASE62 = string.ascii_letters + string.digits  # a-z A-Z 0-9


def _to_base62(data: bytes) -> str:
    """Convert bytes to a Base62 string."""
    n = int.from_bytes(data, "big")
    if n == 0:
        return _BASE62[0]
    chars: list[str] = []
    while n:
        n, remainder = divmod(n, 62)
        chars.append(_BASE62[remainder])
    return "".join(reversed(chars))


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns
    -------
    (raw_key, key_hash, key_prefix)
        raw_key   — full key, e.g. "sk-AbCd1234..."  (show once, never store)
        key_hash  — SHA-256 hex digest of raw_key    (store this)
        key_prefix — first 12 chars for display       (store this)
    """
    random_bytes = secrets.token_bytes(32)
    token = _to_base62(random_bytes)
    raw_key = f"{settings.API_KEY_PREFIX}{token}"
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[:12]
    return raw_key, key_hash, key_prefix


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    return secrets.compare_digest(hash_api_key(raw_key), stored_hash)


# ── Password hashing（bcrypt 直连）────────────────────────────────────────────
#
# 不走 passlib：当前环境 passlib 1.7.4 与 bcrypt 5.x 不兼容（__about__ 被移除 +
# 72-byte 报错）。直接用 bcrypt 库。bcrypt 上限 72 字节，超出截断。

import bcrypt  # noqa: E402

_BCRYPT_MAX_BYTES = 72


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Return a bcrypt hash (utf-8 str) for *password*."""
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Constant-time bcrypt verify. Returns False if *stored_hash* is empty."""
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(_pw_bytes(password), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── JWT (login tokens)────────────────────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

from jose import JWTError, jwt  # noqa: E402


def create_access_token(
    *,
    subject: str,
    role: str,
    tenant_id: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    """
    Sign an HS256 JWT. `subject` is the user id (str). `role`/`tenant_id` are
    embedded as claims so route guards can authorize without a DB hit (the
    DB load in get_current_user still re-checks is_active).
    """
    s = get_settings()
    minutes = expires_minutes if expires_minutes is not None else s.JWT_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "tenant_id": tenant_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, s.SECRET_KEY, algorithm=s.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode + verify a JWT. Raises ValueError on any failure."""
    s = get_settings()
    try:
        return jwt.decode(token, s.SECRET_KEY, algorithms=[s.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc
