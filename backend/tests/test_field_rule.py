"""
Prompt System v2 — Phase 2: FieldRule model + skeleton render + composer opt-in.
"""

from __future__ import annotations

from types import SimpleNamespace


def test_render_skeleton_emits_only_nonempty_sections():
    from app.ocr_optimizer.service.field_rule import FieldRule, Generalization
    fr = FieldRule(
        semantic="开票方（卖方）公司名称",
        anchors=["票头左上 logo 旁", "邻近 'From:' / 供应商区块"],
        format_rule="原文保留，不翻译；去除随附的注册号",
        disambiguation=["与买方名称区分：买方在 'Bill To' 区块"],
        generalization=Generalization(
            rule="始终取票头供应商区块的第一行公司名",
            evidence_per_sample=["s1: ACME", "s2: BETA", "s3: GAMMA"],
            holds_for_all=True,
        ),
    )
    out = fr.render_skeleton()
    assert "- 语义：开票方" in out
    assert "取值锚点" in out and "邻近 'From:'" in out
    assert "- 格式：" in out
    assert "- 排歧：" in out
    assert "跨样本规则（已覆盖全部样本）" in out


def test_sparse_rule_is_compact_and_renderable_flag():
    from app.ocr_optimizer.service.field_rule import FieldRule
    fr = FieldRule(format_rule="纯数字，去千分位与货币符号")
    out = fr.render_skeleton()
    assert out.strip() == "- 格式：纯数字，去千分位与货币符号"
    assert fr.is_renderable()
    assert not FieldRule().is_renderable()   # empty rule renders nothing


def test_holds_for_all_false_tags_pending():
    from app.ocr_optimizer.service.field_rule import FieldRule, Generalization
    fr = FieldRule(generalization=Generalization(rule="3 字母+数字开头", holds_for_all=False))
    assert "跨样本规则（待更多样本验证）" in fr.render_skeleton()


def test_from_module_lifts_legacy_suggestions():
    from app.ocr_optimizer.service.field_rule import from_module
    m = SimpleNamespace(
        description="发票号码",
        ocr_suggestions={
            "semantics": "发票唯一编号",
            "position": "票头右上 'Invoice No.' 之后",
            "most_common_feature": "字母+数字",
            "extra_features": ["勿与 PO/DO 号混淆"],
        },
    )
    fr = from_module(m)
    assert fr is not None
    out = fr.render_skeleton()
    assert "发票唯一编号" in out
    assert "Invoice No." in out
    assert "排歧" in out


def test_from_module_returns_none_when_nothing_structured():
    from app.ocr_optimizer.service.field_rule import from_module
    m = SimpleNamespace(description="", ocr_suggestions={})
    assert from_module(m) is None


def test_field_rule_of_reads_persisted_copy():
    from app.ocr_optimizer.service.field_rule import FIELD_RULE_KEY, field_rule_of
    m = SimpleNamespace(ocr_suggestions={FIELD_RULE_KEY: {"semantic": "X 字段语义"}})
    fr = field_rule_of(m)
    assert fr is not None and fr.semantic == "X 字段语义"


def test_composer_uses_field_rule_when_present():
    """A module carrying a renderable FieldRule renders the skeleton; a plain
    module renders its raw ocr_prompt (Phase-1 behavior, unchanged)."""
    from app.ocr_optimizer.service import composer
    from app.ocr_optimizer.service.field_rule import FieldRule

    structured = SimpleNamespace(
        module_key="inv_no", display_name="发票号", json_path="$[*].invoiceNumber",
        schema_fragment={"type": "string"}, ocr_prompt="RAW-BLOB-SHOULD-NOT-APPEAR",
        order_index=0,
        field_rule=FieldRule(semantic="发票唯一编号", format_rule="字母+数字"),
    )
    plain = SimpleNamespace(
        module_key="cur", display_name="货币", json_path="$[*].currency",
        schema_fragment={"type": "string"}, ocr_prompt="找币种代码 RAW-PLAIN-BODY",
        order_index=1,
    )
    text = composer.assemble_prompt([structured, plain], country_global="CG")
    # structured → skeleton, raw blob suppressed
    assert "- 语义：发票唯一编号" in text
    assert "RAW-BLOB-SHOULD-NOT-APPEAR" not in text
    # plain → raw body preserved
    assert "找币种代码 RAW-PLAIN-BODY" in text
