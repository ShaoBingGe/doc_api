"""PASS_SCHEMA_TO_MODEL 灰度开关：schema 是否随调用下发到 processor。

背景（docs/plan-schema-not-passed-to-model.md）：生产链路在 prompt 存在时
schema 从未到达模型——开放平台路径压根不传第三参；工作区路径管道修过键名
但调用链恒传 None。而迭代评测（ocr_runner）一直在传，评测与生产不同构。

本文件锁三件事：
  1. 开关关（默认）→ processor 收到 runtime_config=None，与旧行为逐字节等价；
  2. 开关开 → 收到 {"response_schema": <active 版本的 composed_schema>}；
  3. schema 源必须是 active 版本（不是 api_def 行的陈旧快照），版本缺失才回退。
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.services import extract_service as svc


# ── 捕获 processor 收到什么 ───────────────────────────────────────────────────

class _SpyProcessor:
    """记录 process_document 收到的参数，返回合法 JSON。"""

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    def process_document(self, path, instruction, runtime_config=None):
        self.calls.append((instruction, runtime_config))
        return '[{"docType": "invoice", "invoiceNumber": "T-1"}]'

    def get_model_version(self):
        return "spy|spy-model"


@pytest.fixture()
def spy(monkeypatch):
    s = _SpyProcessor()
    from app.processors.factory import ProcessorFactory
    monkeypatch.setattr(ProcessorFactory, "create",
                        classmethod(lambda cls, *a, **kw: s))
    return s


SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT",
          "properties": {"docType": {"type": "STRING"}}}}


# ── _run_processor 层（两条生产链路共用的最终出口）────────────────────────────

def test_switch_off_passes_none_runtime_config(spy, monkeypatch, tmp_path):
    """默认（关）：即便 schema 在手也不下发——与修复前逐字节等价。"""
    monkeypatch.setattr(get_settings(), "PASS_SCHEMA_TO_MODEL", False,
                        raising=False)
    f = tmp_path / "x.pdf"; f.write_bytes(b"%PDF-1.4")
    svc._run_processor(storage_path=str(f), processor_type="mock",
                       model_name=None, prompt="PROMPT", schema=SCHEMA)
    assert spy.calls == [("PROMPT", None)]


def test_switch_on_passes_response_schema(spy, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "PASS_SCHEMA_TO_MODEL", True,
                        raising=False)
    f = tmp_path / "x.pdf"; f.write_bytes(b"%PDF-1.4")
    svc._run_processor(storage_path=str(f), processor_type="mock",
                       model_name=None, prompt="PROMPT", schema=SCHEMA)
    (instruction, runtime), = spy.calls
    assert instruction == "PROMPT", "prompt 仍是 instruction，schema 走 runtime——两者互补而非二选一"
    assert runtime == {"response_schema": SCHEMA}


def test_switch_on_without_schema_still_none(spy, monkeypatch, tmp_path):
    """开关开但该 API 没有 schema → 仍传 None，不下发空壳。"""
    monkeypatch.setattr(get_settings(), "PASS_SCHEMA_TO_MODEL", True,
                        raising=False)
    f = tmp_path / "x.pdf"; f.write_bytes(b"%PDF-1.4")
    svc._run_processor(storage_path=str(f), processor_type="mock",
                       model_name=None, prompt="PROMPT", schema=None)
    assert spy.calls == [("PROMPT", None)]


# ── schema 源：active 版本优先，api_def 行回退 ───────────────────────────────

def _mk_api(db, *, row_schema) -> ApiDefinition:
    api = ApiDefinition(
        id=uuid.uuid4(), name="schema-src-test",
        api_code=f"schema-src-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.active.value, version=1,
        response_schema=row_schema, processor_type="mock", tenant_id=None,
    )
    db.add(api)
    db.commit()
    return api


def _mk_version(db, api, *, schema, status="active"):
    from app.ocr_optimizer.models import OcrPromptVersion
    v = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="v1",
        composed_prompt="COMPOSED-PROMPT", composed_schema=schema,
        status=status,
    )
    db.add(v)
    db.commit()
    return v


def test_schema_source_prefers_active_version(db_session):
    """迭代重组的 composed_schema 不回写 api_def 行——生产必须取版本的，
    否则迭代过的 API 会拿到建 API 时的陈旧字段集（丢 rename/新字段）。"""
    from app.ocr_optimizer import get_active_prompt_and_schema

    stale = {"type": "ARRAY", "note": "建 API 时的旧快照"}
    fresh = {"type": "ARRAY", "note": "迭代后的新 schema"}
    api = _mk_api(db_session, row_schema=stale)
    _mk_version(db_session, api, schema=fresh)

    prompt, schema = get_active_prompt_and_schema(db_session, api.id)
    assert prompt == "COMPOSED-PROMPT"
    assert schema == fresh, "必须取 active 版本的 schema，不是 api_def 行的陈旧快照"


def test_schema_source_falls_back_to_row_when_no_version(db_session):
    """未走优化器的直建 API：无版本 → (None, None)，调用方回退行级 schema。"""
    from app.ocr_optimizer import get_active_prompt_and_schema

    api = _mk_api(db_session, row_schema={"type": "ARRAY"})
    prompt, schema = get_active_prompt_and_schema(db_session, api.id)
    assert prompt is None and schema is None


def test_archived_version_not_used(db_session):
    from app.ocr_optimizer import get_active_prompt_and_schema

    api = _mk_api(db_session, row_schema=None)
    _mk_version(db_session, api, schema={"type": "ARRAY"}, status="archived")
    assert get_active_prompt_and_schema(db_session, api.id) == (None, None)


# ── 工作区路径（reprocess）同一开关 ──────────────────────────────────────────

def test_workspace_call_processor_honors_switch(monkeypatch, tmp_path):
    from app.services import document_service as ds

    captured = {}

    class _P:
        def process_document(self, path, instruction, runtime_config=None):
            captured["runtime"] = runtime_config
            return '{"a": 1}'
        def get_model_version(self):
            return "spy|m"

    from app.processors.factory import ProcessorFactory
    monkeypatch.setattr(ProcessorFactory, "create",
                        classmethod(lambda cls, *a, **kw: _P()))
    f = tmp_path / "x.pdf"; f.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(get_settings(), "PASS_SCHEMA_TO_MODEL", False,
                        raising=False)
    ds._call_processor(str(f), "mock", prompt="P", schema=SCHEMA)
    assert captured["runtime"] is None

    monkeypatch.setattr(get_settings(), "PASS_SCHEMA_TO_MODEL", True,
                        raising=False)
    ds._call_processor(str(f), "mock", prompt="P", schema=SCHEMA)
    assert captured["runtime"] == {"response_schema": SCHEMA}
