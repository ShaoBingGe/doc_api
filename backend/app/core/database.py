"""
SQLAlchemy engine + session factory.

原型阶段：SQLite（同步 Session）。
生产迁移：只需替换 DATABASE_URL 为 postgresql://...，其余代码不变。
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────

_connect_args: dict = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite: 允许跨线程共享连接（FastAPI 多线程环境需要）
    _connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    # 生产 PostgreSQL 时建议设置连接池参数：
    # pool_size=10, max_overflow=20, pool_pre_ping=True
    echo=settings.DEBUG,
)

# Enable WAL mode for SQLite to improve concurrent read performance
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# ── Session Factory ───────────────────────────────────────────────────────────

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Session:
    """
    FastAPI dependency: yields a DB session and ensures it is closed
    even if the request handler raises an exception.
    使用方：`db: Session = Depends(get_db)`
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Create all tables defined in ORM models (used in tests / startup)."""
    from app.models import Base  # noqa: F401 — side-effect import registers metadata
    Base.metadata.create_all(bind=engine)


def _ensure_column(
    table: str,
    column: str,
    *,
    sqlite_type: str,
    pg_type: str | None = None,
    ddl_suffix: str = "",
) -> None:
    """Idempotent「缺列则 ALTER ADD」原型迁移模板（结构审查 F3：此前四个
    ensure_* 函数各复制一份 30 行的 PRAGMA/information_schema 查列样板）。

    `create_all` never ALTERs——每个后加的 ORM 列都需要一条声明。生产
    （PostgreSQL）应换真迁移工具；本模板 prototype-only，每次启动幂等执行。
    永不抛异常（启动路径，失败仅记日志）。
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        is_sqlite = settings.DATABASE_URL.startswith("sqlite")
        try:
            if is_sqlite:
                cols = {row[1] for row in conn.execute(
                    text(f'PRAGMA table_info("{table}")'))}
            else:
                cols = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t"
                        ),
                        {"t": table},
                    )
                }
            if cols and column not in cols:
                col_type = sqlite_type if is_sqlite else (pg_type or sqlite_type)
                ddl = f'ALTER TABLE "{table}" ADD COLUMN {column} {col_type}'
                if ddl_suffix:
                    ddl += f" {ddl_suffix}"
                conn.execute(text(ddl))
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "_ensure_column failed for %s.%s", table, column,
            )


def ensure_customize_job_columns() -> None:
    """customize_jobs.options（JSON，save-as-new 特性）。"""
    _ensure_column("customize_jobs", "options", sqlite_type="TEXT", pg_type="JSON")


def ensure_ocr_module_columns() -> None:
    """ocr_modules.rule_edits_text（TEXT，ADR-002 存量缺口）。"""
    _ensure_column(
        "ocr_modules", "rule_edits_text",
        sqlite_type="TEXT", ddl_suffix="NOT NULL DEFAULT ''",
    )


def ensure_ocr_skill_columns() -> None:
    """ocr_skills.country（VARCHAR，全局技能按国家隔离——daace43 存量缺口：
    老库缺列时技能 CRUD 直接 500）。nullable，NULL=通用全局。"""
    _ensure_column("ocr_skills", "country", sqlite_type="VARCHAR(8)")


def ensure_round_eval_quality_column() -> None:
    """ocr_optimization_rounds.eval_quality（JSON，批次2 评测有效性）。"""
    _ensure_column(
        "ocr_optimization_rounds", "eval_quality",
        sqlite_type="TEXT", pg_type="JSON",
    )


def ensure_tenant_columns() -> None:
    """api_definitions / documents / api_keys 补 tenant_id（多租户隔离）。
    SQLite 用 CHAR(32) 存 UUID；nullable，无需回填。"""
    for table in ("api_definitions", "documents", "api_keys"):
        _ensure_column(table, "tenant_id", sqlite_type="CHAR(32)")


def ensure_external_template_id_column() -> None:
    """api_definitions 补 external_template_id（开放平台数字模板号，如 templateId=7）。
    nullable：为空表示该 API 不对开放平台暴露，无需回填存量行。"""
    _ensure_column("api_definitions", "external_template_id", sqlite_type="INTEGER")
