"""v1 prompt 的字段键名清单 —— 防字段名漂移。

qwen 视觉模型不支持 response_schema 硬约束，字段键名只靠 prompt 文本约束。
v1 版本原先不含字段清单，模型自行起名：实测 invoiceDate→date、
totalNetAmount→subTotal，甚至 totalAmount 留空而只填 grossAmount（读对了值
却填错键，下游取不到数）。本组测试锁住清单必须出现且内容正确。
"""

from __future__ import annotations

from app.ocr_optimizer.service.composer import _render_schema_tree
from app.ocr_optimizer.service.preset_init import (
    _render_field_contract,
    build_v1_prompt,
)

# 国家模板的 yaml 用大写类型名（Gemini schema 风格）——这正是曾让渲染
# 整棵退化成 JSON dump 的原因，故测试必须用大写构造。
MY_LIKE_SCHEMA = {
    "title": "invoice_and_receipts",
    "type": "ARRAY",
    "items": {
        "anyOf": [
            {
                "type": "OBJECT",
                "properties": {
                    "docType": {"type": "STRING", "enum": ["invoice", "receipt"]},
                    "page": {"type": "ARRAY", "items": {"type": "NUMBER"}},
                    "invoiceDate": {"type": "STRING"},
                    "totalAmount": {"type": "NUMBER"},
                    "totalNetAmount": {"type": "NUMBER"},
                    "billFromName": {"type": "STRING"},
                    "detailOfGoodsOrServices": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "articleName": {"type": "STRING"},
                                "grossAmount": {"type": "NUMBER"},
                            },
                        },
                    },
                },
            },
            {
                "type": "OBJECT",
                "properties": {
                    "docType": {"type": "STRING", "enum": ["other"]},
                    "page": {"type": "ARRAY", "items": {"type": "NUMBER"}},
                },
            },
        ]
    },
}


def test_uppercase_types_render_as_tree_not_json_dump():
    """大小写 bug 回归：ARRAY/OBJECT 必须能命中分支，否则退化成 JSON dump。"""
    tree = _render_schema_tree(MY_LIKE_SCHEMA)
    assert not tree.lstrip().startswith("```json"), "不应回退为 JSON dump"
    assert "输出为 JSON 数组" in tree


def test_tree_lists_every_leaf_field():
    """漂移最严重的那几个键必须逐个出现在清单里。"""
    tree = _render_schema_tree(MY_LIKE_SCHEMA)
    for field in ("invoiceDate", "totalAmount", "totalNetAmount",
                  "billFromName", "articleName", "grossAmount"):
        assert field in tree, f"字段清单缺少 {field}"


def test_anyof_branches_both_expanded():
    """items 为 anyOf 时两个分支都要展开——只展开一个会漏掉 other 类型。"""
    tree = _render_schema_tree(MY_LIKE_SCHEMA)
    assert "(选项1)" in tree and "(选项2)" in tree
    assert "invoice | receipt" in tree
    assert "enum: other" in tree


def test_nested_array_items_expanded():
    """明细数组的子字段要展开，否则模型不知道 articleName 该叫什么。"""
    tree = _render_schema_tree(MY_LIKE_SCHEMA)
    assert "detailOfGoodsOrServices[]" in tree
    assert "articleName" in tree


def test_tree_is_far_smaller_than_json_dump():
    """紧凑树的意义在于省 token；退化成 dump 就失去价值。"""
    import json
    tree = _render_schema_tree(MY_LIKE_SCHEMA)
    dump = json.dumps(MY_LIKE_SCHEMA, ensure_ascii=False)
    assert len(tree) < len(dump), "字段树应比 JSON dump 小"


def test_field_contract_states_the_hard_rule():
    """契约段必须明确「键名逐字一致」并点名已知漂移，否则形同虚设。"""
    c = _render_field_contract(MY_LIKE_SCHEMA)
    assert "只能使用下列键名" in c
    assert "严禁自创" in c or "严禁自创、改写" in c
    # 点名实测发生过的漂移，给模型具体的反例
    assert "date" in c and "subTotal" in c and "grossAmount" in c


def test_v1_prompt_contains_rules_contract_and_output_spec():
    """v1 三段齐全且顺序正确：国家规则 → 字段清单 → Part 3 输出契约。"""
    decomposed = {"prompt_format": "# 任务\n识别马来西亚票据。",
                  "json_schema": MY_LIKE_SCHEMA}
    p = build_v1_prompt(decomposed)
    assert "识别马来西亚票据" in p
    assert "输出字段清单" in p
    assert "invoiceDate" in p
    i_rules = p.index("识别马来西亚票据")
    i_fields = p.index("输出字段清单")
    assert i_rules < i_fields, "字段清单应在国家规则之后"


def test_v1_prompt_is_deterministic():
    """同一模板反复组装结果一致——刷新逻辑靠它判断「有无变化」。"""
    d = {"prompt_format": "x", "json_schema": MY_LIKE_SCHEMA}
    assert build_v1_prompt(d) == build_v1_prompt(d)
