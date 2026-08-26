"""Test bootstrap.

Two jobs, both before any app import binds the engine or resolves mappers:

  1. ISOLATE THE TEST DATABASE. Several tests' `_setup_*` helpers call
     db.commit(), which previously persisted fixture rows into the DEV database
     (data/apianything.db) — flooding the real API list with `overlay_test` /
     `phase23_my_test` junk. We point tests at a dedicated, throwaway DB file
     and rebuild it fresh each run, so tests can never touch dev data again.

  2. Warm up SQLAlchemy mapper resolution to dodge the known cold-start
     circular import between app.models and app.ocr_optimizer.models.
"""

import os

# (1) Force a dedicated test DB BEFORE importing anything that reads settings /
# builds the engine. os.environ wins over .env in pydantic-settings, and
# get_settings() is first called when app.core.database is imported below.
_TEST_DB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "test_apianything.db")
)
os.environ["DATABASE_URL"] = "sqlite:///" + _TEST_DB
# Start each run from a clean schema (also remove WAL/SHM sidecars).
for _suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_TEST_DB + _suffix)
    except FileNotFoundError:
        pass

import pytest  # noqa: E402

import app.models  # noqa: F401,E402  — registers all mappers
import app.ocr_optimizer  # noqa: F401,E402  — registers OCR mappers

from app.core.database import create_tables  # noqa: E402

# Build the schema in the fresh test DB.
create_tables()


@pytest.fixture
def db_session():
    """Shared throwaway DB session — rolls back at end of test."""
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


# ── 真实可解析的最小文件 ──────────────────────────────────────────────────────
# 上传校验（services/upload_validation）会真正解析文件，所以测试夹具不能再用
# `b"%PDF-1.4 fake"` 这种占位字节 —— 它们是**结构性损坏**的文件，会被正确拒收。
# 用 PyMuPDF 生成真正合法的最小 PDF/PNG，让用例测的是业务逻辑而不是坏文件路径。

def minimal_pdf(pages: int = 1) -> bytes:
    """→ 合法的 N 页 PDF 字节。"""
    import fitz

    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def minimal_png(w: int = 200, h: int = 150) -> bytes:
    """→ 合法 PNG 字节（尺寸默认在校验下限之上）。"""
    import fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h))
    pix.clear_with(255)
    return pix.tobytes("png")
