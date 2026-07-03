"""反思证据层测试：编辑意图分类（NORMALIZE/RETARGET/...）+ 全文检索.

对应需求 1.1（格式型修正 → 输出规范）/ 1.2（内容型修正 → 全文检索定位真实来源）。
分类器是纯代码（无 LLM），必须可被穷举测试。
"""
from __future__ import annotations

from app.ocr_optimizer.reflection import edit_intent as ei


def _diff(ov, cv, *, on="invoiceNumber", cn="invoiceNumber", of="string", cf="string"):
    return {
        "kind": "edit",
        "module_key": "invoice_number",
        "original_name": on, "corrected_name": cn,
        "original_value": ov, "corrected_value": cv,
        "original_format": of, "corrected_format": cf,
    }


# ── NORMALIZE：仅删字符（1.1 场景）──────────────────────────────────────────

def test_strip_prefix_is_normalize():
    it = ei.classify(_diff("W1 529054", "529054"))
    assert it.intent == "NORMALIZE"
    assert it.removed_prefix == "W1 "
    assert it.removal_pattern == "strip_prefix"
    block = it.render_block()
    assert "前缀" in block and "W1" in block
    assert "输出规范化规则" in block  # 指引：调规范而不是调锚点


def test_strip_scattered_chars_is_normalize():
    it = ei.classify(_diff("6,000.00", "6000.00"))
    assert it.intent == "NORMALIZE"
    assert "," in it.removed_chars
    assert it.removal_pattern == "strip_chars"


def test_strip_suffix_currency():
    it = ei.classify(_diff("600.00 RM", "600.00"))
    assert it.intent == "NORMALIZE"
    assert it.removed_suffix == " RM"


def test_strip_dashes_and_spaces():
    it = ei.classify(_diff("A-123 456", "A123456"))
    assert it.intent == "NORMALIZE"
    assert "-" in it.removed_chars and " " in it.removed_chars


# ── RETARGET：内容实质不同（1.2 场景）───────────────────────────────────────

def test_different_value_is_retarget():
    it = ei.classify(_diff("INV-2024-001", "PO-88123"))
    assert it.intent == "RETARGET"
    block = it.render_block()
    assert "全文检索" in block or "重新定位" in block


def test_rewrite_not_pure_deletion_is_retarget():
    # "12345" → "12346"：不是子序列删除（有改写）
    it = ei.classify(_diff("12345", "12346"))
    assert it.intent == "RETARGET"


# ── 其他意图 ────────────────────────────────────────────────────────────────

def test_rename_only():
    it = ei.classify(_diff("X1", "X1", on="billFromName", cn="supplierName"))
    assert it.intent == "RENAME_ONLY"


def test_case_only():
    it = ei.classify(_diff("abc Ltd", "ABC LTD"))
    assert it.intent == "CASE_ONLY"


def test_type_only():
    it = ei.classify(_diff("42", "42", of="string", cf="number"))
    assert it.intent == "TYPE_ONLY"


def test_mixed_rename_plus_value():
    it = ei.classify(_diff("W1 529054", "529054", on="ref", cn="invoiceNo"))
    assert it.intent == "MIXED"
    assert it.removed_prefix == "W1 "  # 值的子分类证据仍保留


def test_add_kind_is_none():
    it = ei.classify({"kind": "add", "corrected_name": "newField"})
    assert it.intent == "NONE"
    assert it.render_block() == ""


# ── 全文检索（1.2）─────────────────────────────────────────────────────────

_SAMPLE_OUTPUTS = {
    "inv1.pdf": {
        "invoiceNumber": "ABC-001",
        "poNumber": "529054",
        "lineItems": [{"description": "widget", "ref": "W1 529054"}],
    },
    "inv2.pdf": {
        "invoiceNumber": "ABC-002",
        "poNumber": "529777",
    },
}


def test_search_exact_hit():
    hits = ei.search_value_in_outputs("529054", _SAMPLE_OUTPUTS)
    paths = {h["field_path"] for h in hits}
    assert "poNumber" in paths
    assert any(h["match"] == "exact" for h in hits)


def test_search_normalized_hit():
    # "W1529054" 去空格后匹配 lineItems[0].ref 的 "W1 529054"
    hits = ei.search_value_in_outputs("W1529054", _SAMPLE_OUTPUTS)
    assert any(h["match"] == "normalized" for h in hits)
    assert any("lineItems[0].ref" == h["field_path"] for h in hits)


def test_search_miss_renders_not_found_guidance():
    hits = ei.search_value_in_outputs("ZZZZZZ", _SAMPLE_OUTPUTS)
    assert hits == []
    block = ei.render_retrieval_block("ZZZZZZ", hits, searched_samples=2)
    assert "全文未检出" in block


def test_retrieval_block_lists_sources():
    hits = ei.search_value_in_outputs("529054", _SAMPLE_OUTPUTS)
    block = ei.render_retrieval_block("529054", hits, searched_samples=2)
    assert "poNumber" in block
    assert "取值锚点" in block  # 结论指引存在


# ── FieldRule 新字段渲染（2.2/2.3）──────────────────────────────────────────

def test_field_rule_renders_aliases_pattern_enum():
    from app.ocr_optimizer.service.field_rule import FieldRule

    fr = FieldRule(
        semantic="币种",
        aliases=["Currency", "CCY", "货币"],
        value_pattern="^[A-Z]{3}$",
        enum_values=["MYR", "USD", "SGD"],
    )
    text = fr.render_skeleton()
    assert "标签别名" in text and "CCY" in text
    assert "值模式" in text and "^[A-Z]{3}$" in text
    # 批次5：枚举改为建议式（样本实证）而非排他硬约束——LLM 从少量样本
    # 归纳的枚举渲染成「只能输出其中之一」会把新票面合法值逼成错误输出
    assert "常见取值" in text and "MYR | USD | SGD" in text
    assert "只能输出其中之一" not in text
