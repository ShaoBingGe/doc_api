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


def ensure_customize_job_columns() -> None:
    """Idempotent add of `customize_jobs.options` (JSON) for pre-existing DBs.

    Same prototype-only pattern as ensure_tenant_columns: `create_all` never
    ALTERs, so DBs created before the save-as-new feature lack the column.
    JSON is stored as TEXT in SQLite; nullable so old rows need no backfill.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        is_sqlite = settings.DATABASE_URL.startswith("sqlite")
        try:
            if is_sqlite:
                cols = {row[1] for row in conn.execute(text('PRAGMA table_info("customize_jobs")'))}
            else:
                cols = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t"
                        ),
                        {"t": "customize_jobs"},
                    )
                }
            if cols and "options" not in cols:
                col_type = "TEXT" if is_sqlite else "JSON"
                conn.execute(text(f'ALTER TABLE "customize_jobs" ADD COLUMN options {col_type}'))
        except Exception:
            import logging
            logging.getLogger(__name__).exception("ensure_customize_job_columns failed")


def ensure_tenant_columns() -> None:
    """
    Idempotent lightweight migration for the prototype SQLite DB.

    `create_all` never ALTERs an existing table, so a freshly-added `tenant_id`
    column won't appear on a dev DB that predates it. Detect-and-add the column
    on the data-owning tables. Production (PostgreSQL) should use a real
    migration tool; this is prototype-only and safe to run on every boot.
    """
    from sqlalchemy import text

    tables = ("api_definitions", "documents", "api_keys")
    with engine.begin() as conn:
        is_sqlite = settings.DATABASE_URL.startswith("sqlite")
        for table in tables:
            try:
                if is_sqlite:
                    cols = {row[1] for row in conn.execute(text(f'PRAGMA table_info("{table}")'))}
                else:  # PostgreSQL / others
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
                if cols and "tenant_id" not in cols:
                    # SQLite stores UUID columns as CHAR(32); a nullable TEXT
                    # column accepts them and SQLAlchemy's Uuid type handles I/O.
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN tenant_id CHAR(32)'))
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "ensure_tenant_columns failed for table %s", table
                )
