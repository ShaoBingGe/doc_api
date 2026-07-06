"""多行明细 P2：数组列级结构编辑（加/删/改名）全链单测。

覆盖三层：
  1. domain/overlay.record_array_column 的登记语义（幂等、抵消、链坍缩、
     删除改名列携带原名）；
  2. 级联标注（改名改整列跨样本、删除删整列；同前缀字段不误伤）；
  3. customize_fork._clone_module 把 array_columns 应用到 items schema
     （renamed→added→deleted 顺序）+ prompt 列结构变更说明。

修正的隐藏缺陷：列改名此前「半失效」——单元格改名只动一行标注，
schema 纹丝不动（customize_fork 仅顶层 rename 传播）。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.domain import overlay as ov
from app.services import pending_edits_service as pes


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
        id=uuid.uuid4(), name="arraycol", api_code=f"ac-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.draft.value, version=1,
        processor_type="mock", model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api)
    db.flush()
    docs = []
    for i in range(n_docs):
        d = Document(
            id=uuid.uuid4(), filename=f"ac{i}.pdf", file_type="pdf", file_size=1,
            storage_path=f"/tmp/ac-{i}", status="completed", api_definition_id=api.id,
        )
        db.add(d)
        docs.append(d)
    db.commit()
    return api, docs


def _ann(db, doc_id, field_name, value="x"):
    from app.models.annotation import Annotation, AnnotationSource, FieldType
    db.add(Annotation(
        id=uuid.uuid4(), document_id=doc_id, field_name=field_name,
        field_value=value, field_type=FieldType.string.value,
        source=AnnotationSource.ai_detected.value, is_corrected=False,
    ))
    db.commit()


def _names(db, doc_id):
    from app.models.annotation import Annotation
    return sorted(a.field_name for a in db.query(Annotation)
                  .filter(Annotation.document_id == doc_id).all())


ARR = "detailOfGoodsOrServices"


# ── 1. overlay 登记语义 ──────────────────────────────────────────────────────

def test_add_column_idempotent_and_undelete(db_session):
    api, _ = _setup(db_session)
    ov.record_array_column(db_session, api.id, ARR, op="delete", name="qty")
    o = ov.record_array_column(db_session, api.id, ARR, op="add", name="qty", col_type="number")
    # 删后再加 = 撤销删除（不进 added），空壳随即被清理
    assert ARR not in o["array_columns"]
    # 重新加一列
    o = ov.record_array_column(db_session, api.id, ARR, op="add", name="discount", col_type="number")
    o = ov.record_array_column(db_session, api.id, ARR, op="add", name="discount")
    spec = o["array_columns"][ARR]
    assert spec["added"] == [{"name": "discount", "type": "number"}]  # 幂等


def test_add_then_delete_cancels_out(db_session):
    api, _ = _setup(db_session)
    ov.record_array_column(db_session, api.id, ARR, op="add", name="discount")
    o = ov.record_array_column(db_session, api.id, ARR, op="delete", name="discount")
    assert ARR not in o["array_columns"]  # 双向抵消 + 空壳清理


def test_rename_chain_collapse_and_cancel(db_session):
    api, _ = _setup(db_session)
    ov.record_array_column(db_session, api.id, ARR, op="rename", name="qty", new_name="quantity2")
    o = ov.record_array_column(db_session, api.id, ARR, op="rename", name="quantity2", new_name="qtyFinal")
    assert o["array_columns"][ARR]["renamed"] == {"qty": "qtyFinal"}  # A→B,B→C ⇒ A→C
    o = ov.record_array_column(db_session, api.id, ARR, op="rename", name="qtyFinal", new_name="qty")
    assert ARR not in o["array_columns"]  # 改回原名 = 取消


def test_delete_renamed_column_records_original_name(db_session):
    """删除改名后的列：deleted 必须携带 schema 里的原列名，否则 fork 删不掉。"""
    api, _ = _setup(db_session)
    ov.record_array_column(db_session, api.id, ARR, op="rename", name="qty", new_name="quantity2")
    o = ov.record_array_column(db_session, api.id, ARR, op="delete", name="quantity2")
    spec = o["array_columns"][ARR]
    assert spec["deleted"] == ["qty"]
    assert spec["renamed"] == {}


def test_rename_of_session_added_column_edits_entry(db_session):
    api, _ = _setup(db_session)
    ov.record_array_column(db_session, api.id, ARR, op="add", name="disc", col_type="number")
    o = ov.record_array_column(db_session, api.id, ARR, op="rename", name="disc", new_name="discount")
    spec = o["array_columns"][ARR]
    assert spec["added"] == [{"name": "discount", "type": "number"}]
    assert spec["renamed"] == {}  # 新增列改名不留 rename 记录


# ── 2. 级联标注 ──────────────────────────────────────────────────────────────

def test_rename_cascades_whole_column_across_docs(db_session):
    api, docs = _setup(db_session)
    for d in docs:
        _ann(db_session, d.id, f"{ARR}[0].qty")
        _ann(db_session, d.id, f"{ARR}[1].qty")
        _ann(db_session, d.id, f"{ARR}[0].desc")
    pes_out = pes.record_array_column(db_session, api.id, ARR, op="rename",
                                      name="qty", new_name="quantity2")
    assert pes_out["array_columns"][ARR]["renamed"] == {"qty": "quantity2"}
    for d in docs:
        names = _names(db_session, d.id)
        assert f"{ARR}[0].quantity2" in names and f"{ARR}[1].quantity2" in names
        assert all(".qty" not in n for n in names)
        assert f"{ARR}[0].desc" in names  # 其他列不动


def test_delete_cascades_and_spares_similar_prefixes(db_session):
    api, docs = _setup(db_session)
    d = docs[0]
    _ann(db_session, d.id, f"{ARR}[0].qty")
    _ann(db_session, d.id, f"{ARR}[0].qtyUnit")        # 同前缀列，不得误伤
    _ann(db_session, d.id, f"{ARR}Total[0].qty")        # 同前缀数组，不得误伤
    pes.record_array_column(db_session, api.id, ARR, op="delete", name="qty")
    names = _names(db_session, d.id)
    assert f"{ARR}[0].qty" not in names
    assert f"{ARR}[0].qtyUnit" in names
    assert f"{ARR}Total[0].qty" in names


def test_locked_array_refuses_column_edit(db_session, monkeypatch):
    api, docs = _setup(db_session)
    _ann(db_session, docs[0].id, f"{ARR}[0].qty")
    monkeypatch.setattr(pes, "_locked_set", lambda db, aid: {ARR})
    o = pes.record_array_column(db_session, api.id, ARR, op="rename",
                                name="qty", new_name="q2")
    assert ARR not in (o.get("array_columns") or {})
    assert f"{ARR}[0].qty" in _names(db_session, docs[0].id)  # 未级联


# ── 3. fork 应用到 items schema + prompt ─────────────────────────────────────

def _array_module():
    return SimpleNamespace(
        module_key="line_items", display_name="商品明细",
        description="明细", json_path=f"$[*].{ARR}[*]",
        schema_fragment={"type": "OBJECT", "properties": {
            "desc": {"type": "STRING"}, "qty": {"type": "NUMBER"},
            "remark": {"type": "STRING"},
        }},
        ocr_suggestions={}, ocr_prompt="BASE 识别明细表",
        skill_ids=[], order_index=3, status="active",
    )


def test_clone_module_applies_array_columns_to_items_schema():
    from app.ocr_optimizer.service.customize_fork import _clone_module

    cloned = _clone_module(_array_module(), new_version_id=uuid.uuid4(), patch={
        "__array_columns": {
            "renamed": {"qty": "quantity2"},
            "added": [{"name": "discount", "type": "number"}],
            "deleted": ["remark"],
        },
    })
    props = cloned.schema_fragment["properties"]
    assert "quantity2" in props and props["quantity2"] == {"type": "NUMBER"}  # 改名保类型
    assert "qty" not in props
    assert props["discount"] == {"type": "NUMBER"}
    assert "remark" not in props
    # prompt 附加列结构变更说明 + 基体保留
    assert "BASE 识别明细表" in cloned.ocr_prompt
    assert "列结构变更" in cloned.ocr_prompt
    assert "quantity2" in cloned.ocr_prompt and "discount" in cloned.ocr_prompt


def test_clone_module_without_array_columns_unchanged():
    from app.ocr_optimizer.service.customize_fork import _clone_module

    src = _array_module()
    cloned = _clone_module(src, new_version_id=uuid.uuid4(), patch={})
    assert cloned.schema_fragment == src.schema_fragment
    assert cloned.ocr_prompt == src.ocr_prompt


def test_schema_roundtrip_after_column_edit():
    """列编辑后 assemble_schema：response_schema 的行结构反映新列集。"""
    from app.ocr_optimizer.service.customize_fork import _clone_module
    from app.ocr_optimizer.service import composer

    cloned = _clone_module(_array_module(), new_version_id=uuid.uuid4(), patch={
        "__array_columns": {"renamed": {}, "added": [{"name": "discount", "type": "number"}],
                            "deleted": ["remark"]},
    })
    schema = composer.assemble_schema([cloned])
    items = schema["items"]["properties"][ARR]["items"]["properties"]
    assert set(items.keys()) == {"desc", "qty", "discount"}


# ── 4. apply_draft Case 7 ────────────────────────────────────────────────────

def test_apply_draft_array_column_case(db_session):
    api, docs = _setup(db_session)
    _ann(db_session, docs[0].id, f"{ARR}[0].qty")
    out = pes.apply_draft(db_session, api.id, {"array_column": {
        "array": ARR, "op": "rename", "name": "qty", "new_name": "quantity2"}})
    assert out["array_columns"][ARR]["renamed"] == {"qty": "quantity2"}
    assert f"{ARR}[0].quantity2" in _names(db_session, docs[0].id)
