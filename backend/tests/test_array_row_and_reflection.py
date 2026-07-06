"""多行明细 P3：列级反思语料 + 行级 GT 删除单测。

历史缺陷：
  - reflection_context 对含 [/. 的字段名直接跳过 → 数组列的修正拿不到
    任何跨样本对照；
  - 数组单元格修正逐 cell 一条孤立文本进 prompt（同列 N 处修正是同一条
    列规则的证据）；
  - 删行留 GT 空洞（缺失 idx 重组成空 dict，evaluator 对齐产幻影行）。
"""
from __future__ import annotations

import uuid

import pytest

from app.domain import overlay as ov
from app.services import pending_edits_service as pes

ARR = "detailOfGoodsOrServices"


@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _setup(db, n_docs=2):
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document

    api = ApiDefinition(
        id=uuid.uuid4(), name="p3", api_code=f"p3-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.draft.value, version=1,
        processor_type="mock", model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api)
    db.flush()
    docs = []
    for i in range(n_docs):
        d = Document(
            id=uuid.uuid4(), filename=f"p3-{i}.pdf", file_type="pdf", file_size=1,
            storage_path=f"/tmp/p3-{i}", status="completed", api_definition_id=api.id,
        )
        db.add(d)
        docs.append(d)
    api.config = {"sample_document_ids": [str(d.id) for d in docs]}
    db.commit()
    return api, docs


def _ann(db, doc_id, field_name, value="x", corrected=False):
    from app.models.annotation import Annotation, AnnotationSource, FieldType
    db.add(Annotation(
        id=uuid.uuid4(), document_id=doc_id, field_name=field_name,
        field_value=value, field_type=FieldType.string.value,
        source=AnnotationSource.ai_detected.value, is_corrected=corrected,
    ))
    db.commit()


def _names(db, doc_id):
    from app.models.annotation import Annotation
    return sorted(a.field_name for a in db.query(Annotation)
                  .filter(Annotation.document_id == doc_id).all())


# ── 1. 列级跨样本语料 ────────────────────────────────────────────────────────

def test_cross_doc_context_collects_array_column(db_session):
    from app.ocr_optimizer.service.reflection_context import (
        _build_cross_doc_context_for_diffs,
    )
    api, docs = _setup(db_session)
    for i, d in enumerate(docs):
        _ann(db_session, d.id, f"{ARR}[0].qty", value=f"1{i}")
        _ann(db_session, d.id, f"{ARR}[1].qty", value=f"2{i}")
        _ann(db_session, d.id, f"{ARR}[0].qtyUnit", value="PCS")  # 同前缀列不混入

    diffs = [{"kind": "edit", "module_key": "line_items",
              "original_name": f"{ARR}[0].qty", "corrected_name": f"{ARR}[0].qty",
              "original_value": "10", "corrected_value": "11"}]
    ctx = _build_cross_doc_context_for_diffs(db_session, api.id, diffs)
    # 语料按 diff 的 cell 名挂载，含该列全部样本全部行的值
    rows = ctx.get(f"{ARR}[0].qty") or []
    values = {r["value"] for r in rows}
    assert values == {"10", "20", "11", "21"}
    assert all("qtyUnit" not in str(r) for r in rows)


def test_cross_doc_context_scalar_path_unchanged(db_session):
    from app.ocr_optimizer.service.reflection_context import (
        _build_cross_doc_context_for_diffs,
    )
    api, docs = _setup(db_session)
    _ann(db_session, docs[0].id, "invoiceNumber", value="INV-1")
    diffs = [{"kind": "edit", "module_key": "invoice_number",
              "original_name": "invoiceNumber", "corrected_name": "invoiceNumber",
              "original_value": "X", "corrected_value": "INV-1"}]
    ctx = _build_cross_doc_context_for_diffs(db_session, api.id, diffs)
    assert [r["value"] for r in ctx["invoiceNumber"]] == ["INV-1"]


# ── 2. cell 修正按列聚合进 prompt ────────────────────────────────────────────

def test_fork_aggregates_cell_fixes_by_column():
    from types import SimpleNamespace
    from app.ocr_optimizer.service.customize_fork import _CELL_NAME_RE

    # 直接验证正则 + 聚合渲染逻辑走 _clone_module 的 __prompt_suffix 太重，
    # 这里验证 cell 名解析边界（聚合流在 fork 集成测试覆盖）。
    assert _CELL_NAME_RE.match(f"{ARR}[0].qty").groups() == (ARR, "qty")
    assert _CELL_NAME_RE.match(f"{ARR}[12].unitPrice").groups() == (ARR, "unitPrice")
    assert _CELL_NAME_RE.match("plainField") is None
    assert _CELL_NAME_RE.match(f"{ARR}[0]") is None          # 无列段
    assert _CELL_NAME_RE.match(f"a.b[0].c") is None           # 嵌套路径不聚合
    _ = SimpleNamespace  # keep import used


# ── 3. 行级 GT 删除 + 重排 ───────────────────────────────────────────────────

def test_delete_array_row_renumbers_following_rows(db_session):
    api, docs = _setup(db_session, n_docs=1)
    d = docs[0]
    for i in range(3):
        _ann(db_session, d.id, f"{ARR}[{i}].qty", value=f"q{i}")
        _ann(db_session, d.id, f"{ARR}[{i}].desc", value=f"d{i}")
    _ann(db_session, d.id, "invoiceNumber", value="INV-1")  # 无关字段不动

    n = ov.delete_array_row(db_session, d.id, ARR, 1)
    assert n == 2  # 该行两列
    names = _names(db_session, d.id)
    # 行 0 不动；原行 2 前移为行 1；无行 2 残留
    assert f"{ARR}[0].qty" in names and f"{ARR}[1].qty" in names
    assert all(not x.startswith(f"{ARR}[2]") for x in names)
    assert "invoiceNumber" in names
    # 值跟着行走：新行 1 是原行 2 的值
    from app.models.annotation import Annotation
    v = (db_session.query(Annotation)
         .filter(Annotation.document_id == d.id,
                 Annotation.field_name == f"{ARR}[1].qty").one())
    assert v.field_value == "q2"


def test_delete_array_row_gt_has_no_hole(db_session):
    """删行后 GT rebuild 不产生幻影空行（这是重排的目的）。"""
    from app.ocr_optimizer.service import ground_truth
    api, docs = _setup(db_session, n_docs=1)
    d = docs[0]
    for i in range(3):
        _ann(db_session, d.id, f"{ARR}[{i}].qty", value=f"q{i}", corrected=True)
    ov.delete_array_row(db_session, d.id, ARR, 0)
    gt = ground_truth.build(db_session, d.id)
    rows = gt[ARR]
    assert len(rows) == 2
    assert [r["qty"] for r in rows] == ["q1", "q2"]  # 无 {} 空洞


def test_apply_draft_array_row_delete_case(db_session):
    api, docs = _setup(db_session, n_docs=1)
    d = docs[0]
    _ann(db_session, d.id, f"{ARR}[0].qty", value="q0")
    _ann(db_session, d.id, f"{ARR}[1].qty", value="q1")
    pes.apply_draft(db_session, api.id, {"array_row": {
        "op": "delete", "document_id": str(d.id), "array": ARR, "index": 0}})
    names = _names(db_session, d.id)
    assert names == [f"{ARR}[0].qty"]  # 原行1 → 行0
