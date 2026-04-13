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
