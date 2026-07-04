"""批次1 后续：存量版本 composed_schema 根形状回填。

修复只对新组装版本生效；早于修复组装的版本带 object 根 schema，
Gemini 链路全字段假 0 分（A/B 实测 0.00 → 0.725）。回填必须：
幂等、只动 $[*] 家族且根非 array 的非 archived 版本、不碰 prompt/模块。
"""
from __future__ import annotations

import uuid

from app.ocr_optimizer.models import (
    OcrModule, OcrPromptVersion, PromptVersionStatus,
)
from app.ocr_optimizer.service.persistence import (
    backfill_composed_schema_root_shape,
)


def _mk_version(db, *, status, schema, json_path="$[*].invoiceNumber"):
    api_id = uuid.uuid4()
    v = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api_id, version="1",
        status=status, composed_prompt="GLOBAL_PREAMBLE\np", composed_schema=schema,
    )
    db.add(v)
    db.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=v.id, module_key="invoice_number",
        display_name="发票号", json_path=json_path,
        ocr_prompt="find", description="", schema_fragment={"type": "string"},
        order_index=1, status="active",
    ))
    db.commit()
    return v


def test_backfill_fixes_object_root_on_array_family(db_session):
    v = _mk_version(
        db_session, status=PromptVersionStatus.active.value,
        schema={"type": "object", "properties": {"invoiceNumber": {"type": "string"}}},
    )
    # 共享测试库里可能残留其他测试的坏 schema 版本（一并被修属正确行为），
    # 断言只关注目标版本与幂等性，不咬全局条数。
    fixed = backfill_composed_schema_root_shape(db_session)
    assert fixed >= 1
    db_session.refresh(v)
    assert v.composed_schema["type"] == "array"
    assert v.composed_schema["items"]["properties"]["invoiceNumber"] == {"type": "string"}
    # 幂等：第二次跑不再改
    assert backfill_composed_schema_root_shape(db_session) == 0


def test_backfill_skips_archived_and_object_family(db_session):
    va = _mk_version(
        db_session, status=PromptVersionStatus.archived.value,
        schema={"type": "object", "properties": {}},
    )
    vo = _mk_version(
        db_session, status=PromptVersionStatus.active.value,
        schema={"type": "object", "properties": {"invoiceNumber": {"type": "string"}}},
        json_path="$.invoiceNumber",   # 对象根家族本来就该是 object
    )
    assert backfill_composed_schema_root_shape(db_session) == 0
    db_session.refresh(va)
    db_session.refresh(vo)
    assert va.composed_schema["type"] == "object"   # archived 不动
    assert vo.composed_schema["type"] == "object"   # $.x 家族不动
