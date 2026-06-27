"""DB 层集成测试 —— P4 采收 find_promotion_candidates + P5 build_insights（SKT-PD）。

补齐之前只用真实生产冒烟、未写合成单测的两个 DB 函数（FK 链 + 国家过滤 + 守护 + 挂载技能）。
"""
import uuid

import pytest


@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _api(db, *, country="JP", tenant=None):
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus

    api = ApiDefinition(
        id=uuid.uuid4(), name=f"api-{uuid.uuid4().hex[:6]}", api_code=f"c-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.draft.value, version=1,
        processor_type="mock", model_name="mock",
        response_schema={"type": "object", "properties": {}},
        tenant_id=(tenant or uuid.uuid4()), config={"source_country": country},
    )
    db.add(api)
    db.flush()
    return api


def _active_version_with_module(db, api, module_key):
    from app.ocr_optimizer.models import (
        OcrModule, OcrPromptVersion, PromptVersionStatus, VersionOrigin,
    )

    ver = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="1",
        status=PromptVersionStatus.active.value, origin=VersionOrigin.init.value,
        composed_prompt="x", composed_schema={}, country_global_text="",
    )
    db.add(ver)
    db.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=ver.id, module_key=module_key,
        display_name=module_key, description="d", json_path=f"$.{module_key}",
        schema_fragment={}, ocr_suggestions={}, ocr_prompt="p", order_index=1,
    ))
    db.flush()
    return ver


def _run_with_iterations(db, api, ver, *, module_key, accs, skill_feedback=None):
    from app.ocr_optimizer.models import (
        OcrModuleIteration, OcrOptimizationRound, OcrOptimizationRun,
    )

    run = OcrOptimizationRun(
        id=uuid.uuid4(), api_definition_id=api.id, starting_version_id=ver.id,
        status="completed", max_rounds=3, target_accuracy=0.95,
        sample_document_ids=[], llm_provider="mock",
    )
    db.add(run)
    db.flush()
    for rn, acc in enumerate(accs, start=1):
        rnd = OcrOptimizationRound(
            id=uuid.uuid4(), run_id=run.id, round_num=rn,
            prompt_version_id=ver.id, overall_accuracy=acc, phase="completed",
        )
        db.add(rnd)
        db.flush()
        db.add(OcrModuleIteration(
            id=uuid.uuid4(), round_id=rnd.id, module_id=uuid.uuid4(),
            module_key=module_key, per_sample_results=[],
            aggregate_accuracy=acc, skill_feedback=skill_feedback,
        ))
    db.commit()
    return run


# ── P4 采收 ─────────────────────────────────────────────────────────────────


def test_find_candidates_groups_and_filters_by_country(db_session):
    from app.ocr_optimizer.service import skill_promotion

    api = _api(db_session, country="JP")
    ver = _active_version_with_module(db_session, api, "currency")
    _run_with_iterations(
        db_session, api, ver, module_key="currency", accs=[0.5, 0.6],
        skill_feedback="需要 ISO 4217 校验技能",
    )

    jp = skill_promotion.find_promotion_candidates(db_session, country="JP")
    cur = [c for c in jp if c.field == "currency"]
    assert cur, "should surface the currency candidate"
    assert cur[0].occurrence_count == 2  # two iterations carried skill_feedback
    assert cur[0].tenant_count == 1
    assert cur[0].recommended is False   # single tenant → not auto-recommended

    # country filter excludes JP candidates
    my = skill_promotion.find_promotion_candidates(db_session, country="MY")
    assert all(c.country == "MY" for c in my)


def test_find_candidates_skips_empty_feedback(db_session):
    from app.ocr_optimizer.service import skill_promotion

    api = _api(db_session, country="JP")
    ver = _active_version_with_module(db_session, api, "invoice_number")
    _run_with_iterations(
        db_session, api, ver, module_key="invoice_number", accs=[0.5, 0.6],
        skill_feedback=None,  # no asks → no candidate
    )
    cands = [
        c for c in skill_promotion.find_promotion_candidates(db_session, country="JP")
        if c.field == "invoice_number"
    ]
    assert cands == []


# ── P5 洞察 ─────────────────────────────────────────────────────────────────


def test_build_insights_trajectory_guardian_and_skills(db_session):
    from app.ocr_optimizer.service import skill_insights, skill_service

    api = _api(db_session, country="JP")
    ver = _active_version_with_module(db_session, api, "total_tax_amount")
    _run_with_iterations(
        db_session, api, ver, module_key="total_tax_amount", accs=[1.0, 1.0, 1.0],
    )
    sk = skill_service.create_skill(
        db_session, name=f"s-{uuid.uuid4().hex[:6]}", content="金额取整", api_def_id=api.id,
    )
    skill_service.attach_skill_to_module(db_session, ver.id, "total_tax_amount", sk.id)

    ins = skill_insights.build_insights(db_session, api.id)
    assert ins["has_run"] is True
    f = next(x for x in ins["fields"] if x["field"] == "total_tax_amount")
    assert f["trajectory"] == [1.0, 1.0, 1.0]
    assert f["guardian"]["kind"] == "pin"   # stable at 100% → pin
    assert sk.name in f["skills"]


def test_build_insights_caution_on_regression(db_session):
    from app.ocr_optimizer.service import skill_insights

    api = _api(db_session, country="JP")
    ver = _active_version_with_module(db_session, api, "line_items")
    _run_with_iterations(
        db_session, api, ver, module_key="line_items", accs=[0.5, 0.8, 0.6],
    )
    ins = skill_insights.build_insights(db_session, api.id)
    f = next(x for x in ins["fields"] if x["field"] == "line_items")
    assert f["guardian"]["kind"] == "caution"  # peak 0.8 → 0.6 regressed
