"""激活版本时把 `api_def.response_schema` 同步成 active 版本的 composed_schema。

历史缺陷（plan-schema-not-passed-to-model.md §0.5）：迭代/定制每建新版本都会
重组 composed_schema，但激活只同步 prompt_version_id **不同步 schema** ——
行级 response_schema 长期停在建 API 时的快照，rename / 新增字段全丢。

生产提取与 API 文档已改为优先读 active 版本，但仍有两处读行级：
  - module_initializer（重新分解模块）
  - customize_fork / save-as-new clone（复制 API）
它们拿到陈旧字段集就会把错误一路传下去。这里从源头消除分裂。
"""

from __future__ import annotations

import uuid

import pytest

from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.ocr_optimizer.models import OcrPromptVersion, PromptVersionStatus
from app.ocr_optimizer.service import persistence

STALE = {"type": "ARRAY", "marker": "建 API 时的旧快照"}
FRESH = {"type": "ARRAY", "marker": "迭代重组后的新 schema"}


def _api(db, schema=STALE) -> ApiDefinition:
    a = ApiDefinition(
        id=uuid.uuid4(), name="sync-test",
        api_code=f"sync-{uuid.uuid4().hex[:6]}", description="",
        status=ApiDefinitionStatus.active.value, version=1,
        response_schema=schema, processor_type="mock", tenant_id=None,
    )
    db.add(a); db.commit()
    return a


def _ver(db, api, *, schema, status=PromptVersionStatus.archived.value, num="1"):
    v = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version=num,
        composed_prompt="P", composed_schema=schema, status=status,
    )
    db.add(v); db.commit()
    return v


def test_activate_syncs_row_schema(db_session):
    api = _api(db_session)
    v = _ver(db_session, api, schema=FRESH)

    persistence.activate_version(db_session, api.id, v.id)

    db_session.refresh(api)
    assert api.prompt_version_id == v.id
    assert api.response_schema == FRESH, "行级 schema 必须跟着激活版本走"


def test_activate_without_schema_keeps_row_value(db_session):
    """目标版本没有 composed_schema（手工建的空版本）→ 保留原值，不置空。"""
    api = _api(db_session)
    v = _ver(db_session, api, schema=None)

    persistence.activate_version(db_session, api.id, v.id)

    db_session.refresh(api)
    assert api.response_schema == STALE, "不能被 None 覆盖成空"


def test_switching_back_restores_that_versions_schema(db_session):
    """回滚到旧版本时，行级 schema 也回滚——两者始终是同一份。"""
    api = _api(db_session)
    v_old = _ver(db_session, api, schema=STALE, num="1")
    v_new = _ver(db_session, api, schema=FRESH, num="2")

    persistence.activate_version(db_session, api.id, v_new.id)
    db_session.refresh(api)
    assert api.response_schema == FRESH

    persistence.activate_version(db_session, api.id, v_old.id)
    db_session.refresh(api)
    assert api.response_schema == STALE
    db_session.refresh(v_new)
    assert v_new.status == PromptVersionStatus.archived.value


def test_row_and_active_version_never_diverge_after_activate(db_session):
    """不变量：激活后，行级 schema 与 active 版本的 composed_schema 恒等。"""
    from app.ocr_optimizer import get_active_prompt_and_schema

    api = _api(db_session)
    v = _ver(db_session, api, schema=FRESH)
    persistence.activate_version(db_session, api.id, v.id)

    db_session.refresh(api)
    _, active_schema = get_active_prompt_and_schema(db_session, api.id)
    assert active_schema == api.response_schema
