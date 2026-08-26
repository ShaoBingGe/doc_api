"""
ApiAnything FastAPI application entry point.

启动:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

API 文档:
    http://localhost:8000/docs      (Swagger UI)
    http://localhost:8000/redoc     (ReDoc)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import v1_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers

settings = get_settings()


def _configure_logging() -> None:
    """让 `app.*` 的 INFO 日志真正输出。

    此前进程里**没有任何日志配置**：Python 根 logger 默认 WARNING，且无 handler，
    只有"最后手段 handler"把 WARNING+ 打到 stderr。结果是代码里所有
    `logger.info(...)` 在生产环境从未出现过——"异步任务完成"、"Calling Gemini
    model"、准入闸就绪、文本层是否命中，全被静默吞掉，排障时只能看到报错。

    做法：给根装一个 handler（level 0，只做输出不过滤），根自身留在 WARNING
    以免 sqlalchemy / httpx / google-genai 的 INFO 刷屏；再单独把 `app` 命名空间
    抬到 INFO。propagate 的记录只受**祖先 handler 的 level** 约束、不再看祖先
    logger 的 level，所以这个组合能精确放行我们自己的 INFO。
    """
    level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("app").setLevel(level)


_configure_logging()


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB tables exist (development convenience)
    from app.core.database import (
        create_tables,
        ensure_customize_job_columns,
        ensure_external_template_id_column,
        ensure_ocr_module_columns,
        ensure_ocr_skill_columns,
        ensure_round_eval_quality_column,
        ensure_tenant_columns,
    )
    create_tables()
    # Idempotent prototype migration: add tenant_id to data tables if missing.
    ensure_tenant_columns()
    # Idempotent: add api_definitions.external_template_id (开放平台 templateId).
    ensure_external_template_id_column()
    # Idempotent: add customize_jobs.options (save-as-new feature) if missing.
    ensure_customize_job_columns()
    # Idempotent: add ocr_optimization_rounds.eval_quality (批次2) if missing.
    ensure_round_eval_quality_column()
    # Idempotent: add ocr_modules.rule_edits_text (ADR-002 存量缺口) if missing.
    ensure_ocr_module_columns()
    # Idempotent: add ocr_skills.country (全局技能国家隔离，daace43 存量缺口) if missing.
    ensure_ocr_skill_columns()
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
            backfill_bare_array_module_items,
            backfill_composed_schema_root_shape,
        )
        _db2 = _SL()
        try:
            backfill_composed_schema_root_shape(_db2)
            # 多行明细 P0：给存量「ARRAY 无 items」的客户数组模块补 items。
            backfill_bare_array_module_items(_db2)
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
    # 开放平台异步任务 worker：常驻协程，负责抢占 PENDING 任务并调度提取。
    # 启动时先把上次非正常退出留下的 RUNNING 退回 PENDING（异步接口文档第 8 条
    # 「服务重启，未完成的任务会被自动恢复处理」）。失败不阻塞服务启动 ——
    # 同步端点不依赖它。
    try:
        from app.services.async_task_worker import start_worker
        await start_worker()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("async task worker failed to start")
    yield
    # Shutdown: 停 worker，在途任务退回 PENDING 由下次启动接手
    try:
        from app.services.async_task_worker import stop_worker
        await stop_worker()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("async task worker failed to stop")


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

# 开放平台（piaozone 兼容）：路径与线上生产一致，**不带 /api/v1 前缀**
# —— POST /base/oauth/token 与 POST /ai/knowledge/nlpService/document/analyze
from app.api.v1.open_api import router as open_api_router  # noqa: E402

app.include_router(open_api_router)

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
