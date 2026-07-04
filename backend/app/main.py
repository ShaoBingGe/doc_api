"""
ApiAnything FastAPI application entry point.

启动:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

API 文档:
    http://localhost:8000/docs      (Swagger UI)
    http://localhost:8000/redoc     (ReDoc)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import v1_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers

settings = get_settings()


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB tables exist (development convenience)
    from app.core.database import (
        create_tables,
        ensure_customize_job_columns,
        ensure_ocr_module_columns,
        ensure_round_eval_quality_column,
        ensure_tenant_columns,
    )
    create_tables()
    # Idempotent prototype migration: add tenant_id to data tables if missing.
    ensure_tenant_columns()
    # Idempotent: add customize_jobs.options (save-as-new feature) if missing.
    ensure_customize_job_columns()
    # Idempotent: add ocr_optimization_rounds.eval_quality (批次2) if missing.
    ensure_round_eval_quality_column()
    # Idempotent: add ocr_modules.rule_edits_text (ADR-002 存量缺口) if missing.
    ensure_ocr_module_columns()
    # Ensure the super admin exists (default admin / 666666). Idempotent.
    try:
        from app.core.database import SessionLocal
        from app.services.user_service import bootstrap_super_admin
        _db = SessionLocal()
        try:
            bootstrap_super_admin(_db)
        finally:
            _db.close()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("bootstrap_super_admin failed on boot")
    # Idempotent 存量回填（批次1 后续）：旧 composer 组装的 object 根
    # composed_schema → 重组为数组根，否则 Gemini 链路全字段假 0 分。
    try:
        from app.core.database import SessionLocal as _SL
        from app.ocr_optimizer.service.persistence import (
            backfill_composed_schema_root_shape,
        )
        _db2 = _SL()
        try:
            backfill_composed_schema_root_shape(_db2)
        finally:
            _db2.close()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("composed_schema backfill failed on boot")
    # Reap any customize jobs that were stuck in transient states when the
    # process died (no progress for >STALE_OPTIMIZING_MIN minutes). Failures
    # here are non-fatal; we log and continue.
    try:
        from app.ocr_optimizer.service.customer_iteration import reap_stale_jobs
        reaped = reap_stale_jobs()
        if reaped:
            import logging
            logging.getLogger(__name__).info(
                "Reaped %d stale customize jobs on boot", reaped,
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("reap_stale_jobs failed on boot")
    yield
    # Shutdown: nothing to clean up in sync mode


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "通用文档结构化数据提取 API 平台。\n\n"
        "上传文档 → AI 提取结构化数据 → 生成可调用的提取 API。\n\n"
        "**管理 API** (`/api/v1/`) 通过 JWT Bearer Token 认证（原型阶段暂不校验）。\n"
        "**公有提取 API** (`/api/v1/extract/`) 通过 `X-API-Key` Header 认证。"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception Handlers ────────────────────────────────────────────────────────

register_exception_handlers(app)

# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(v1_router)

# Serve uploaded files as static assets (prototype only — use CDN / presigned URLs in prod)
import os
from pathlib import Path

_upload_dir = Path(settings.UPLOAD_DIR)
_upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/uploads", StaticFiles(directory=str(_upload_dir)), name="uploads")


# ── Health Check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"], summary="健康检查")
def health_check() -> dict:
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "processor": settings.DEFAULT_PROCESSOR,
    }
