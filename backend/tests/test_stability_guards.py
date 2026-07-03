"""批次6 回归：抗噪与稳定性加固。

  1. reconciler 矛盾检测门（红线⑤「矛盾才协调」的代码执行——历史逢新建议
     必 LLM 重写，prompt 每轮不可复现漂移）；
  2. 版本选择平局带（观测分差小于半个量化步长是采样噪声，持平保早版）；
  3. optimizer 重写的客户反馈保留性守护（红线⑤「累积不覆盖」round 路径）；
  4. 绝对坐标硬校验（§3.5 泛化守护从口头约定变成代码）。
"""
from __future__ import annotations

import uuid

from app.ocr_optimizer.service import reconciler
from app.ocr_optimizer.service.field_rule import (
    FieldRule, has_absolute_coordinates, sanitize_field_rule,
)
from app.ocr_optimizer.service.module_optimizer import customer_feedback_preserved


# ── 1. 矛盾检测门 ────────────────────────────────────────────────────────────

_PROMPT_WITH_FEEDBACK = "找发票号\n\n# 客户反馈补充\n- 取括号内的值\n- 输出去掉前缀 W1"


def test_opposite_directives_detected_as_contradiction():
    assert reconciler.has_contradiction(_PROMPT_WITH_FEEDBACK, ["请取括号外的值"]) is True


def test_same_topic_different_rule_is_contradiction():
    assert reconciler.has_contradiction(_PROMPT_WITH_FEEDBACK, ["输出保留前缀 W1"]) is True


def test_unrelated_new_suggestion_is_not_contradiction():
    assert reconciler.has_contradiction(
        _PROMPT_WITH_FEEDBACK, ["日期统一输出 YYYY-MM-DD"],
    ) is False


def test_duplicate_suggestion_is_not_contradiction():
    assert reconciler.has_contradiction(
        _PROMPT_WITH_FEEDBACK, ["取括号内的值"],
    ) is False


def test_no_accumulated_feedback_never_contradicts():
    assert reconciler.has_contradiction("裸 prompt 无反馈块", ["取括号外的值"]) is False


# ── 2. 版本选择平局带 ────────────────────────────────────────────────────────

def _mk_run(db, evaluated):
    from app.ocr_optimizer.models import (
        OcrOptimizationRun, OcrOptimizationRound, RunStatus, RoundPhase,
    )
    run = OcrOptimizationRun(
        id=uuid.uuid4(), api_definition_id=uuid.uuid4(),
        starting_version_id=evaluated[0][0], status=RunStatus.running.value,
        sample_document_ids=[], llm_provider="mock|",
    )
    db.add(run)
    db.flush()
    for i, (pv, acc) in enumerate(evaluated, start=1):
        db.add(OcrOptimizationRound(
            id=uuid.uuid4(), run_id=run.id, round_num=i,
            prompt_version_id=pv, overall_accuracy=acc,
            # 3 样本 × 20 字段 → 量化步长 1/60 ≈ 0.0167，半步 ≈ 0.0083
            per_sample_accuracy={"a": 0.9, "b": 0.9, "c": 0.9},
            phase=RoundPhase.completed.value,
        ))
    db.commit()
    return run


def test_noise_level_improvement_keeps_earlier_version(db_session):
    """v2 只比 v1 高 0.003（远小于半个量化步长）→ 噪声级差异，保早版。"""
    from app.ocr_optimizer.service.customer_iteration import _best_evaluated_version
    v1, v2 = uuid.uuid4(), uuid.uuid4()
    _run = _mk_run(db_session, [(v1, 0.900), (v2, 0.903)])
    best_id, _ = _best_evaluated_version(db_session, _run.id)
    assert best_id == v1


def test_real_improvement_still_switches_version(db_session):
    from app.ocr_optimizer.service.customer_iteration import _best_evaluated_version
    v1, v2 = uuid.uuid4(), uuid.uuid4()
    _run = _mk_run(db_session, [(v1, 0.80), (v2, 0.90)])
    best_id, best_acc = _best_evaluated_version(db_session, _run.id)
    assert best_id == v2 and best_acc == 0.90


def test_regression_still_keeps_best(db_session):
    from app.ocr_optimizer.service.customer_iteration import _best_evaluated_version
    v1, v2, v3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _run = _mk_run(db_session, [(v1, 0.70), (v2, 0.90), (v3, 0.75)])
    best_id, _ = _best_evaluated_version(db_session, _run.id)
    assert best_id == v2


# ── 3. 客户反馈保留性守护 ────────────────────────────────────────────────────

_OLD = (
    "找总金额。\n\n# 客户反馈补充\n"
    "- 金额输出去掉 RM 前缀与千分位\n"
    "- 负数保留减号\n"
)


def test_rewrite_keeping_feedback_passes():
    new = "找总金额（合计行）。\n金额输出去掉 RM 前缀与千分位。\n负数保留减号。"
    assert customer_feedback_preserved(_OLD, new) is True


def test_rewrite_dropping_feedback_fails():
    new = "找票面右下角的总金额，输出数字。"
    assert customer_feedback_preserved(_OLD, new) is False


def test_prompt_without_feedback_always_passes():
    assert customer_feedback_preserved("裸 prompt", "任意重写") is True


# ── 4. 绝对坐标硬校验 ────────────────────────────────────────────────────────

def test_coordinate_patterns_detected():
    assert has_absolute_coordinates("该字段位于第 3 行") is True
    assert has_absolute_coordinates("位置 (120, 45) 附近") is True
    assert has_absolute_coordinates("x=120 y=45") is True
    assert has_absolute_coordinates("参考 bbox 区域") is True
    # 相对锚点不误伤
    assert has_absolute_coordinates("'Invoice No.' 标签右侧") is False
    assert has_absolute_coordinates("票面右上角区块") is False


def test_sanitize_drops_coordinate_anchors_keeps_relative():
    fr = FieldRule(
        semantic="s",
        anchors=["'Invoice No.' 右侧", "第 3 行第 2 列", "票头区块"],
    )
    out = sanitize_field_rule(fr)
    assert out is not None
    assert out.anchors == ["'Invoice No.' 右侧", "票头区块"]


def test_reflector_drops_coordinate_fix_suggestions(monkeypatch):
    from app.ocr_optimizer.reflection import reflector

    def fake_llm(**kwargs):
        return {
            "rationale": "r",
            "fix_suggestion": "该字段固定在第 3 行，直接取第 3 行内容",
        }

    monkeypatch.setattr(reflector, "llm_text_completion_failover", fake_llm)
    results = reflector.reflect_on_diffs(
        [{"kind": "edit", "module_key": "inv",
          "original_name": "inv", "corrected_name": "inv",
          "original_value": "A", "corrected_value": "B"}],
        modules_by_key={"inv": {"description": "", "ocr_prompt": ""}},
        processor_spec="mock",
    )
    assert results["inv"].fix_suggestions == []
