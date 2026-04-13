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
