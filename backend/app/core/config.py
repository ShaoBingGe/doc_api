"""
Application configuration via Pydantic BaseSettings.

读取顺序: 环境变量 > .env 文件 > 默认值
原型阶段默认 SQLite + 本地文件存储 + 同步任务运行。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "ApiAnything"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ── Database ──────────────────────────────────────────────────────────
    # 原型：SQLite；生产：postgresql+asyncpg://...
    DATABASE_URL: str = "sqlite:///./data/apianything.db"

    # ── File Storage ──────────────────────────────────────────────────────
    STORAGE_BACKEND: str = "local"          # local | s3
    UPLOAD_DIR: str = "./data/uploads"      # LocalStorage 存储目录
    MAX_UPLOAD_SIZE_MB: int = 20

    # S3（仅 STORAGE_BACKEND=s3 时生效）
    S3_BUCKET: str = ""
    S3_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # ── Task Runner ───────────────────────────────────────────────────────
    TASK_RUNNER: str = "sync"               # sync | celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"

    # ── AI Processors ─────────────────────────────────────────────────────
    DEFAULT_PROCESSOR: str = "mock"         # mock | gemini | openai
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "CHANGE-ME-IN-PRODUCTION-32-bytes!!"
    API_KEY_PREFIX: str = "sk-"

    # ── CORS ──────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── Pagination ────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    @field_validator("UPLOAD_DIR", mode="before")
    @classmethod
    def ensure_upload_dir(cls, v: str) -> str:
        Path(v).mkdir(parents=True, exist_ok=True)
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
