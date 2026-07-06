"""多行明细 P0：客户新增带列数组字段 → 正确的数组模块 + items schema.

覆盖：
  - _module_from_add_diff 的 array 分支（json_path=$[*].{name}[*]、fragment=items）
  - 有列 → items.object.properties 齐全；无列 → items=STRING（裸值数组）
  - assemble_schema round-trip：record.items.properties.{arr}.items.properties.{col}
  - overlay.record_added_field 存 columns；apply_draft 透传
  - 存量「ARRAY 无 items」模块回填
"""
from __future__ import annotations

import uuid

import pytest

from app.ocr_optimizer.service import composer
from app.ocr_optimizer.service.customize_fork import _module_from_add_diff


def _add_diff(name, columns=None, fmt="array"):
    d = {"kind": "add", "corrected_name": name, "corrected_format": fmt}
    if columns is not None:
        d["columns"] = columns
    return d


# ── _module_from_add_diff array 分支 ─────────────────────────────────────────

def test_add_array_with_columns_builds_items_object():
    mod = _module_from_add_diff(
        _add_diff("feeDetails", [{"name": "feeName", "type": "string"},
                                 {"name": "amount", "type": "number"}]),
        new_version_id=uuid.uuid4(), order_index=5, reflection_outputs=None,
        processor_spec="mock", model_name=None,
    )
    assert mod.json_path == "$[*].feeDetails[*]"
    frag = mod.schema_fragment
    assert frag["type"] == "OBJECT"
    assert frag["properties"]["feeName"] == {"type": "STRING"}
    assert frag["properties"]["amount"] == {"type": "NUMBER"}
    # prompt 提到列清单
    assert "feeName" in mod.ocr_prompt and "amount" in mod.ocr_prompt
    assert "空数组 []" in mod.ocr_prompt


def test_add_array_without_columns_is_string_items():
    mod = _module_from_add_diff(
        _add_diff("tags", columns=[]),
        new_version_id=uuid.uuid4(), order_index=1, reflection_outputs=None,
        processor_spec="mock", model_name=None,
    )
    assert mod.json_path == "$[*].tags[*]"
    assert mod.schema_fragment == {"type": "STRING"}


def test_add_array_date_column_carries_format():
    mod = _module_from_add_diff(
        _add_diff("schedule", [{"name": "dueDate", "type": "date"}]),
        new_version_id=uuid.uuid4(), order_index=1, reflection_outputs=None,
        processor_spec="mock", model_name=None,
    )
    col = mod.schema_fragment["properties"]["dueDate"]
    assert col["type"] == "STRING" and col.get("format") == "date"


def test_add_scalar_field_unchanged():
    mod = _module_from_add_diff(
        _add_diff("supplierTier", fmt="string"),
        new_version_id=uuid.uuid4(), order_index=1, reflection_outputs=None,
        processor_spec="mock", model_name=None,
    )
    assert mod.json_path == "$[*].supplierTier"
    assert mod.schema_fragment == {"type": "STRING"}


def test_add_array_dedups_and_drops_empty_columns():
    mod = _module_from_add_diff(
        _add_diff("x", [{"name": "a"}, {"name": "a", "type": "number"},
                        {"name": ""}, {"type": "string"}]),
        new_version_id=uuid.uuid4(), order_index=1, reflection_outputs=None,
        processor_spec="mock", model_name=None,
    )
    # 只保留首个 a（string 默认），去重去空
    assert list(mod.schema_fragment["properties"].keys()) == ["a"]
    assert mod.schema_fragment["properties"]["a"] == {"type": "STRING"}


# ── assemble_schema round-trip（新数组 + 标量 + 模板数组共存）────────────────

def test_assemble_schema_with_customer_array_group():
    from types import SimpleNamespace

    def m(key, jp, frag):
        return SimpleNamespace(module_key=key, json_path=jp, schema_fragment=frag, display_name=key)

    added = _module_from_add_diff(
        _add_diff("feeDetails", [{"name": "feeName", "type": "string"},
                                 {"name": "amount", "type": "number"}]),
        new_version_id=uuid.uuid4(), order_index=9, reflection_outputs=None,
        processor_spec="mock", model_name=None,
    )
    mods = [
        m("inv", "$[*].invoiceNumber", {"type": "string"}),
        m("goods", "$[*].detailOfGoodsOrServices[*]",
          {"type": "object", "properties": {"desc": {"type": "string"}}}),
        added,  # 客户新增的第二个数组组
    ]
    schema = composer.assemble_schema(mods)
    assert schema["type"] == "array"
    props = schema["items"]["properties"]
    # 三者共存：标量 + 模板数组 + 客户新增数组
    assert props["invoiceNumber"] == {"type": "string"}
    assert props["detailOfGoodsOrServices"]["type"] == "array"
    fee = props["feeDetails"]
    assert fee["type"] == "array"
    assert fee["items"]["properties"]["feeName"] == {"type": "STRING"}
    assert fee["items"]["properties"]["amount"] == {"type": "NUMBER"}


# ── overlay.record_added_field 存 columns ────────────────────────────────────

@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _mk_api(db):
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    api = ApiDefinition(
        id=uuid.uuid4(), name="arr", api_code=f"arr-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.draft.value, version=1,
        processor_type="mock", model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api)
    db.commit()
    return api


def test_overlay_stores_columns_for_array_field(db_session):
    from app.domain import overlay
    api = _mk_api(db_session)
    ov = overlay.record_added_field(
        db_session, api.id, "feeDetails", field_type="array",
        columns=[{"name": "feeName", "type": "string"}, {"name": "amount", "type": "number"}],
    )
    entry = next(f for f in ov["added_fields"] if f["field_name"] == "feeDetails")
    assert entry["columns"] == [{"name": "feeName", "type": "string"},
                                {"name": "amount", "type": "number"}]


def test_overlay_ignores_columns_for_scalar_field(db_session):
    from app.domain import overlay
    api = _mk_api(db_session)
    ov = overlay.record_added_field(
        db_session, api.id, "note", field_type="string",
        columns=[{"name": "x", "type": "string"}],
    )
    entry = next(f for f in ov["added_fields"] if f["field_name"] == "note")
    assert "columns" not in entry


def test_apply_draft_passes_columns(db_session):
    from app.services import pending_edits_service as pes
    api = _mk_api(db_session)
    ov = pes.apply_draft(db_session, api.id, {
        "new_name": "feeDetails", "field_type": "array",
        "columns": [{"name": "amount", "type": "number"}],
    })
    entry = next(f for f in ov["added_fields"] if f["field_name"] == "feeDetails")
    assert entry["columns"] == [{"name": "amount", "type": "number"}]


# ── 存量裸数组回填 ───────────────────────────────────────────────────────────

def test_backfill_bare_array_module_items(db_session):
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus
    from app.ocr_optimizer.service.persistence import backfill_bare_array_module_items

    api = _mk_api(db_session)
    v = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="1",
        status=PromptVersionStatus.active.value,
        composed_prompt="p", composed_schema={"type": "array", "items": {"type": "object", "properties": {}}},
    )
    db_session.add(v)
    db_session.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=v.id, module_key="tags",
        display_name="tags",
        # 旧 _module_from_add_diff 约定：数组字段 json_path=$[*].{name}（非 [*] 结尾），
        # fragment 是整个数组的 schema（ARRAY 无 items = 零行约束）。
        json_path="$[*].tags",
        ocr_prompt="find", description="", schema_fragment={"type": "ARRAY"},
        order_index=1, status="active",
    ))
    db_session.commit()

    n = backfill_bare_array_module_items(db_session)
    assert n >= 1
    m = db_session.query(OcrModule).filter(OcrModule.prompt_version_id == v.id).first()
    assert m.schema_fragment == {"type": "ARRAY", "items": {"type": "STRING"}}
