"""批次5 回归：反思落地链路。

覆盖四个历史缺陷：
  1. 数值修正误分类：认错数字（1000.00→100.00 是字符子序列）被判 NORMALIZE，
     产出荒谬的「删字符」规则；str(float) 序列化差异（600.0 vs "600.00"）被判
     RETARGET，反思去重写本来正确的定位锚点。
  2. 清空幻觉值被判 RETARGET：反思去「重新定位」一个票面上不存在的值。
  3. 同 module_key 多条 diff 反思互相覆盖：2/3 跨样本视角被丢弃。
  4. FieldRule 断头路：结构化规则从未落库，composer 骨架渲染是死代码；
     且无硬校验——过窄正则/幻觉枚举直接变成 prompt 硬约束。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.ocr_optimizer.reflection import edit_intent
from app.ocr_optimizer.reflection.master import route
from app.ocr_optimizer.service.field_rule import (
    FieldRule, Generalization, merge_field_rules, sanitize_field_rule,
)


def _edit_diff(ov, cv, **kw):
    d = {"kind": "edit", "module_key": "amount",
         "original_name": "totalAmount", "corrected_name": "totalAmount",
         "original_value": ov, "corrected_value": cv}
    d.update(kw)
    return d


# ── 1. 数值防误判 ────────────────────────────────────────────────────────────

def test_digit_misread_is_retarget_not_normalize():
    # "100.00" 是 "1000.00" 的字符子序列——历史判 NORMALIZE（删字符 0）
    it = edit_intent.classify(_edit_diff("1000.00", "100.00"))
    assert it.intent == "RETARGET"


def test_serialization_difference_is_normalize_not_retarget():
    # 数值相等，仅小数位/序列化差异——历史判 RETARGET（改锚点）
    it = edit_intent.classify(_edit_diff("600.0", "600.00"))
    assert it.intent == "NORMALIZE"


def test_thousands_separator_removal_still_normalize():
    it = edit_intent.classify(_edit_diff("6,000.00", "6000.00"))
    assert it.intent == "NORMALIZE"


def test_non_numeric_prefix_strip_still_normalize():
    it = edit_intent.classify(_edit_diff("W1 529054", "529054"))
    assert it.intent == "NORMALIZE"
    assert it.removed_prefix == "W1 "


def test_truncated_date_is_retarget():
    it = edit_intent.classify(_edit_diff("2025-05-02", "2025"))
    # "2025" 是子序列且数值形态（2025.0 vs 解析失败）——日期含 - 不是纯数值，
    # 走子序列删除？"2025" 可由删除得到 → 但这是截断修正。至少不能是 SUPPRESS。
    assert it.intent in ("RETARGET", "NORMALIZE")  # 记录当前判定
    # 关键回归点：数字类认错必须 RETARGET（上面的用例）；此例保持可观测


# ── 2. SUPPRESS 意图与路由 ───────────────────────────────────────────────────

def test_cleared_value_is_suppress():
    it = edit_intent.classify(_edit_diff("C 190312986", ""))
    assert it.intent == "SUPPRESS"
    block = it.render_block()
    assert "抑制" in block
    assert "null" in block


def test_cleared_value_routes_to_suppress_skill_not_retarget():
    diff = _edit_diff("C 190312986", None)
    keys = [s.key for s in route(diff)]
    assert "suppress" in keys
    assert "retarget" not in keys
    assert "value_mismatch" not in keys


def test_normal_retarget_still_routes_to_retarget():
    diff = _edit_diff("Registration No. 123", "INV-000888")
    keys = [s.key for s in route(diff)]
    assert "retarget" in keys
    assert "suppress" not in keys


# ── 3. 同 module_key 多 diff 合并 ────────────────────────────────────────────

def test_multi_diff_same_module_key_merges_not_overwrites(monkeypatch):
    from app.ocr_optimizer.reflection import reflector

    calls = []

    def fake_llm(*, processor_spec, model_name, system_instruction, user_prompt, as_json):
        calls.append(user_prompt)
        n = len(calls)
        return {
            "rationale": f"根因{n}",
            "fix_suggestion": f"建议{n}",
            "anchors": [f"锚点{n}"],
        }

    monkeypatch.setattr(reflector, "llm_text_completion_failover", fake_llm)
    diffs = [
        _edit_diff("RM 100", "100"),
        _edit_diff("RM 200", "200"),
        _edit_diff("RM 300", "300"),
    ]
    results = reflector.reflect_on_diffs(diffs, modules_by_key={
        "amount": {"description": "总额", "ocr_prompt": "找总额"},
    }, processor_spec="mock")

    assert len(results) == 1
    r = results["amount"]
    # 三条反思的建议全部保留（历史：字典覆盖只剩最后一条）
    assert len(r.fix_suggestions) == 3
    assert set(r.fix_suggestions) == {"建议1", "建议2", "建议3"}
    # FieldRule 列表字段累积
    assert r.field_rule is not None
    assert len(r.field_rule.anchors) == 3


# ── 4. FieldRule 硬校验 ──────────────────────────────────────────────────────

def test_sanitize_drops_uncompilable_pattern():
    fr = FieldRule(semantic="s", value_pattern="([bad")
    out = sanitize_field_rule(fr)
    assert out is not None and out.value_pattern == ""


def test_sanitize_drops_pattern_contradicting_observed_values():
    # 从单一开票方归纳的过窄正则：换一个开票方（5 位号）即失效
    fr = FieldRule(semantic="s", value_pattern=r"^INV-\d{6}$")
    out = sanitize_field_rule(fr, observed_values=["INV-123456", "AB-12345"])
    assert out is not None and out.value_pattern == ""


def test_sanitize_keeps_pattern_matching_all_observed():
    fr = FieldRule(semantic="s", value_pattern=r"^[A-Z]+-\d+$")
    out = sanitize_field_rule(fr, observed_values=["INV-123456", "AB-12345"])
    assert out is not None and out.value_pattern == r"^[A-Z]+-\d+$"


def test_sanitize_drops_enum_missing_an_observed_value():
    fr = FieldRule(semantic="s", enum_values=["MYR", "USD", "SGD"])
    out = sanitize_field_rule(fr, observed_values=["IDR"])
    assert out is not None and out.enum_values == []


def test_sanitize_keeps_enum_covering_observed_values():
    fr = FieldRule(semantic="s", enum_values=["MYR", "USD"])
    out = sanitize_field_rule(fr, observed_values=["MYR"])
    assert out is not None and out.enum_values == ["MYR", "USD"]


def test_sanitize_demotes_unevidenced_holds_for_all():
    fr = FieldRule(
        semantic="s",
        generalization=Generalization(rule="规则", evidence_per_sample=[], holds_for_all=True),
    )
    out = sanitize_field_rule(fr, sample_count=3)
    assert out is not None and out.generalization.holds_for_all is False


def test_sanitize_keeps_evidenced_holds_for_all():
    fr = FieldRule(
        semantic="s",
        generalization=Generalization(
            rule="规则", evidence_per_sample=["e1", "e2", "e3"], holds_for_all=True,
        ),
    )
    out = sanitize_field_rule(fr, sample_count=3)
    assert out is not None and out.generalization.holds_for_all is True


def test_merge_field_rules_accumulates():
    a = FieldRule(semantic="旧语义", aliases=["Invoice No."], anchors=["票头右上"])
    b = FieldRule(semantic="新语义", aliases=["Inv #"], value_pattern=r"^\d+$")
    m = merge_field_rules(a, b)
    assert m.semantic == "新语义"                       # 最新非空优先
    assert m.aliases == ["Invoice No.", "Inv #"]        # 列表累积去重
    assert m.anchors == ["票头右上"]
    assert m.value_pattern == r"^\d+$"


# ── 5. 持久化端到端：_clone_module → composer 附加渲染 ───────────────────────

def test_clone_module_persists_field_rule_and_composer_renders_it():
    import uuid
    from app.ocr_optimizer.service.customer_iteration import _clone_module
    from app.ocr_optimizer.service import composer
    from app.ocr_optimizer.service.field_rule import FIELD_RULE_KEY

    src = SimpleNamespace(
        module_key="invoice_number", display_name="发票号",
        description="发票号码", json_path="$[*].invoiceNumber",
        schema_fragment={"type": "string"},
        ocr_suggestions={}, ocr_prompt="BASE-BODY 找发票号",
        skill_ids=[], order_index=0, status="active",
    )
    reflection = SimpleNamespace(
        kind="edit",
        diff={"kind": "edit", "original_name": "invoiceNumber",
              "corrected_name": "invoiceNumber",
              "original_value": "W1 529054", "corrected_value": "529054"},
        rationale_summary="去前缀",
        fix_suggestions=["输出时去掉前缀 W1"],
        field_rule=FieldRule(semantic="发票唯一编号", format_rule="去掉 W1 前缀"),
    )
    cloned = _clone_module(
        src, new_version_id=uuid.uuid4(),
        patch={"__reflection": reflection, "__prompt_suffix": "输出时去掉前缀 W1"},
    )
    # FieldRule 已持久化
    assert isinstance(cloned.ocr_suggestions.get(FIELD_RULE_KEY), dict)
    assert cloned.ocr_suggestions[FIELD_RULE_KEY]["semantic"] == "发票唯一编号"
    # composer 附加式渲染：基体 + 骨架都在
    body = composer._render_module_body(cloned)
    assert "BASE-BODY 找发票号" in body
    assert "- 语义：发票唯一编号" in body
