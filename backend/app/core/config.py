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
    # Default Gemini model used when an ApiDefinition's model_name is null
    # (production OCR) and as the first chain step's model when
    # LLM_FALLBACK_CHAIN isn't configured. Sensible Q2-2026 default.
    GEMINI_MODEL: str = "gemini-2.5-flash"
    OPENAI_API_KEY: str = ""
    # 阿里云百炼 / DashScope（qwen-vl-ocr 视觉 OCR + qwen-plus 文本），OpenAI 兼容端点。
    # 大陆云实例用它替代不可达的 Gemini。
    QWEN_API_KEY: str = ""
    QWEN_MODEL: str = "qwen-vl-plus"         # 生产 OCR（视觉）；vl-plus 抽取质量/速度均衡
    QWEN_TEXT_MODEL: str = "qwen-plus"       # 反思/优化（文本）
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # Failover chain for reflection / optimizer LLM calls. Semicolon-separated
    # `provider|model` entries; first one that succeeds wins. The chain ONLY
    # applies to text-completion calls (reflection, module_optimizer,
    # meta_optimizer), NOT to the production OCR call (which uses the
    # ApiDefinition's configured processor).
    # Example: "gemini|gemini-2.5-flash;openai|gpt-4o-mini;mock|"
    LLM_FALLBACK_CHAIN: str = ""

    # ── Skill optimization (ADR-001) ─────────────────────────────────────────
    # Held-out validation gate for iteration rounds: optimize on a TRAIN split,
    # score/select versions on a held-out VAL split (the noise samples) so a
    # round's "improvement" must generalize, not overfit the same samples.
    # Default OFF — when off, run_orchestrator behaves byte-identically.
    SKILL_HELDOUT_GATE: bool = False
    # Fraction of samples reserved as the held-out val split (the trailing
    # samples — anchors stay in train). Min 1 val sample.
    SKILL_HELDOUT_VAL_FRAC: float = 0.25

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "CHANGE-ME-IN-PRODUCTION-32-bytes!!"
    API_KEY_PREFIX: str = "sk-"

    # ── Auth / JWT（角色与权限管理）────────────────────────────────────────
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 12          # 登录令牌有效期（12h）
    # 安装时创建的超级管理员（原型默认值，生产环境务必改）
    SUPER_ADMIN_USERNAME: str = "admin"
    SUPER_ADMIN_PASSWORD: str = "666666"
    # 普通用户「邮箱+验证码」登录的固定验证码（原型阶段；接邮件下发后改）
    NORMAL_USER_LOGIN_CODE: str = "666666"

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
