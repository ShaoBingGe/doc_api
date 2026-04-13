"""
PiaoZone API token manager with caching and auto-refresh.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class PiaoZoneTokenManager:
    """管理 PiaoZone API 的 token 获取和缓存。"""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._client_id: Optional[str] = os.environ.get("PIAOZONE_CLIENT_ID")
        self._client_secret: Optional[str] = os.environ.get("PIAOZONE_CLIENT_SECRET")
        self._token_url: str = os.environ.get(
            "PIAOZONE_TOKEN_URL",
            "https://api-sit.piaozone.com/base/oauth/token",
        )
        self._token_duration_hours: int = int(
            os.environ.get("PIAOZONE_TOKEN_DURATION_HOURS", "24")
        )

    def _calculate_sign(self, timestamp: int) -> str:
        raw = f"{self._client_id}{self._client_secret}{timestamp}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _fetch_new_token(self) -> dict:
        if not self._client_id or not self._client_secret:
            raise ValueError("PIAOZONE_CLIENT_ID and PIAOZONE_CLIENT_SECRET must be set")

        timestamp = int(time.time())
        payload = {
            "client_id": self._client_id,
            "timestamp": str(timestamp),
            "sign": self._calculate_sign(timestamp),
        }

        logger.info("Fetching new token from PiaoZone API")
        resp = requests.post(
            self._token_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()

        if not resp.text.strip():
            raise RuntimeError("Empty response from PiaoZone token API")

        data = resp.json()
        logger.info("Successfully fetched new token from PiaoZone API")
        return data

    def get_token(self) -> str:
        # Static token takes priority (backward-compatible)
        static = os.environ.get("PIAOZONE_ACCESS_TOKEN")
        if static:
            return static

        # Return cached token if still valid
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token

        token_data = self._fetch_new_token()

        if (
            isinstance(token_data, dict)
            and token_data.get("data")
            and "access_token" in token_data["data"]
        ):
            self._token = token_data["data"]["access_token"]
            self._token_expiry = (
                datetime.now()
                + timedelta(hours=self._token_duration_hours)
                - timedelta(minutes=5)
            )
        elif isinstance(token_data, dict) and "access_token" in token_data:
            self._token = token_data["access_token"]
            expires_in = token_data.get("expires_in", self._token_duration_hours * 3600)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in) - timedelta(minutes=5)
        else:
            raise RuntimeError(f"Unexpected token response format: {token_data}")

        logger.info("Token will expire at %s", self._token_expiry)
        return self._token  # type: ignore[return-value]

    def clear_cache(self) -> None:
        self._token = None
        self._token_expiry = None
        logger.info("Token cache cleared")


_token_manager = PiaoZoneTokenManager()


def get_piaozone_token() -> str:
    """Convenience function — returns a valid PiaoZone access token."""
    return _token_manager.get_token()


def clear_piaozone_token_cache() -> None:
    """Clear cached token (e.g. after an auth error)."""
    _token_manager.clear_cache()
