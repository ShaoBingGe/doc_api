"""
Alembic environment configuration for ApiAnything.

支持:
  - 离线模式 (alembic upgrade --sql): 生成纯 SQL 脚本
  - 在线模式 (alembic upgrade head):  直连数据库执行迁移

DATABASE_URL 读取优先级: 环境变量 > .env 文件 > alembic.ini 默认值
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── 确保 app 包可被导入 ────────────────────────────────────────────────────────
# alembic 从 backend/ 运行时 sys.path 已包含 backend/，但 Docker / CI 中不一定
_backend_dir = Path(__file__).resolve().parent.parent  # backend/
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# ── 导入 ORM metadata（注册所有模型）────────────────────────────────────────────
from app.models import Base  # noqa: E402 — must come after sys.path fix

# ── Alembic Config object ──────────────────────────────────────────────────────
config = context.config

# 将 alembic.ini 中的 [loggers] 配置应用到 Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 指向所有模型的 MetaData，用于 --autogenerate
target_metadata = Base.metadata

# ── 从环境变量/Settings 覆盖数据库 URL ──────────────────────────────────────────
def _get_database_url() -> str:
    """读取 DATABASE_URL：环境变量 > app.core.config > alembic.ini 默认值。"""
    # 1. 直接读环境变量（Docker / CI 注入）
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # 2. 通过 pydantic-settings（会读 .env 文件）
    try:
        from app.core.config import get_settings
        return get_settings().DATABASE_URL
    except Exception:
        pass
    # 3. alembic.ini 中的默认值
    return config.get_main_option("sqlalchemy.url", "sqlite:///./data/apianything.db")


# 注入 URL，让 engine_from_config 使用正确的连接串
config.set_main_option("sqlalchemy.url", _get_database_url())


# ── 离线模式 ───────────────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """生成 SQL 脚本而不实际连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── 在线模式 ───────────────────────────────────────────────────────────────────

def run_migrations_online() -> None:
    """连接数据库并执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,          # 检测列类型变更
            compare_server_default=True,  # 检测默认值变更
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
