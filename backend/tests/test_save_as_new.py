"""「另存为新模板」(save_as_new) — 已发布 API 的再迭代与模板分叉.

需求（线上反馈）：已发布的 API 模板继续上传文件要按模板触发识别；用户修改/
新增/删除字段应走新一轮优化迭代，且可以选择优化原模板（api_code 不变，
Phase 19 原地 bump）或保存为新的 API 模板（本测试覆盖的新路径）。

隔离红线：save_as_new 的克隆带样本文档与标注的行级副本 —— 后续对克隆的
字段级联改名绝不能反向污染源工作区的 Ground Truth。
"""
from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings


@pytest.fixture()
def mock_env(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "DEFAULT_PROCESSOR", "mock", raising=False)
    monkeypatch.setattr(s, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(s, "LLM_FALLBACK_CHAIN", "mock|", raising=False)
    yield s


def _setup_published_api(db, *, n_samples: int = 3):
    from app.models.annotation import Annotation
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document, DocumentStatus
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus

    api = ApiDefinition(
        id=uuid.uuid4(), name="saveasnew-src", api_code=f"san-{uuid.uuid4().hex[:8]}",
        status=ApiDefinitionStatus.active,  # 已发布
        processor_type="gemini", model_name="gemini-2.5-flash",
        config={"sample_document_ids": []},
    )
    db.add(api)
    ver = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="1",
        status=PromptVersionStatus.active.value,
        composed_prompt="GLOBAL_PREAMBLE\nextract",
        composed_schema={"type": "object", "properties": {"invoiceNumber": {"type": "string"}}},
    )
    db.add(ver)
    db.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=ver.id, module_key="invoice_number",
        display_name="发票号", json_path="$.invoiceNumber",
        ocr_prompt="find invoice number", description="invoice no",
        schema_fragment={"type": "string"}, order_index=1, status="active",
    ))
    sample_ids = []
    for i in range(n_samples):
        doc = Document(
            id=uuid.uuid4(), filename=f"s{i}.pdf", file_type="pdf", file_size=100,
            status=DocumentStatus.completed, storage_path=f"/tmp/san-{i}.pdf",
            api_definition_id=api.id,
        )
        db.add(doc)
        db.add(Annotation(
            id=uuid.uuid4(), document_id=doc.id, field_name="invoiceNumber",
            field_value=f"INV-{i:03d}", field_type="string",
            source="manual", is_corrected=True,
        ))
        sample_ids.append(str(doc.id))
    api.config = {"sample_document_ids": sample_ids}
    db.commit()
    return api, ver


def _rename_diff():
    return [{
        "kind": "edit", "module_key": "invoice_number",
        "original_name": "invoiceNumber", "corrected_name": "invoiceNo",
        "original_value": "INV-000", "corrected_value": "INV-000",
        "original_format": "string", "corrected_format": "string",
    }]


def test_save_as_new_clones_and_iterates_without_touching_source(db_session, mock_env):
    from app.models.annotation import Annotation
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document
    from app.ocr_optimizer.models import OcrPromptVersion
    from app.ocr_optimizer.service import customer_iteration as ci

    db = db_session
    api, src_ver = _setup_published_api(db)

    job = ci.submit_customize_job(
        db, source_api_definition_id=api.id, diffs=_rename_diff(),
        options={"save_as_new": True, "new_name": "客户A定制"},
    )
    ci._execute_pipeline(db, job)
    db.refresh(job)
    db.refresh(api)

    # job 完成且指向克隆
    assert job.status == "completed"
    assert job.new_api_definition_id is not None
    assert job.new_api_definition_id != api.id
    assert job.new_api_code and job.new_api_code != api.api_code

    clone = db.get(ApiDefinition, job.new_api_definition_id)
    assert clone is not None
    assert clone.name == "客户A定制"
    assert clone.status == ApiDefinitionStatus.pending_review  # 待客户验证后再发布
    assert (clone.config or {}).get("cloned_from_api_id") == str(api.id)

    # 源完全未动：仍已发布、仅 1 个版本且 active、api_code 不变
    assert api.status == ApiDefinitionStatus.active
    src_versions = (
        db.query(OcrPromptVersion)
        .filter(OcrPromptVersion.api_definition_id == api.id)
        .all()
    )
    assert len(src_versions) == 1
    assert src_versions[0].status == "active"

    # GT 隔离：克隆样本是独立副本；源标注字段名未被级联改名
    clone_sample_ids = (clone.config or {}).get("sample_document_ids") or []
    src_sample_ids = (api.config or {}).get("sample_document_ids") or []
    assert len(clone_sample_ids) == len(src_sample_ids) == 3
    assert set(clone_sample_ids).isdisjoint(set(src_sample_ids))

    src_ann_names = {
        a.field_name
        for a in db.query(Annotation)
        .join(Document, Annotation.document_id == Document.id)
        .filter(Document.api_definition_id == api.id)
        .all()
    }
    assert src_ann_names == {"invoiceNumber"}

    # 克隆上产生了迭代版本（≥ 复制v1 + 定制v2）
    clone_versions = (
        db.query(OcrPromptVersion)
        .filter(OcrPromptVersion.api_definition_id == clone.id)
        .count()
    )
    assert clone_versions >= 2


def test_save_as_new_api_code_suffix_increments(db_session, mock_env):
    from app.ocr_optimizer.service import customer_iteration as ci

    db = db_session
    api, _ = _setup_published_api(db)

    c1 = ci._clone_api_for_save_as_new(db, api, new_name="c1")
    c2 = ci._clone_api_for_save_as_new(db, api, new_name="c2")
    assert c1.api_code == f"{api.api_code}-c1"
    assert c2.api_code == f"{api.api_code}-c2"


def test_in_place_customize_still_default(db_session, mock_env):
    """不带 save_as_new 时维持 Phase 19 行为：原地 bump，api_code 不变。"""
    from app.ocr_optimizer.service import customer_iteration as ci

    db = db_session
    api, _ = _setup_published_api(db)
    original_code = api.api_code

    job = ci.submit_customize_job(
        db, source_api_definition_id=api.id, diffs=_rename_diff(),
    )
    ci._execute_pipeline(db, job)
    db.refresh(job)
    db.refresh(api)

    assert job.status == "completed"
    assert job.new_api_definition_id == api.id  # Phase 19：等于源
    assert api.api_code == original_code
