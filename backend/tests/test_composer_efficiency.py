"""Composer 编译效率测试：紧凑 Schema 树 + 客户反馈折叠 + reconciler 膨胀阈值.

对应需求 3：剪掉 prompt 内冗余（全量 Schema dump ~28%），字段内识别方法
统筹收敛而非无限堆叠。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.ocr_optimizer.service import composer


def _mod(key, json_path, frag, prompt="找到该字段", order=0):
    return SimpleNamespace(
        module_key=key, display_name=key, json_path=json_path,
        schema_fragment=frag, ocr_prompt=prompt, order_index=order,
        ocr_suggestions={}, description="",
    )


def _two_modules():
    return [
        _mod("invoice_number", "$.invoiceNumber", {"type": "string"}, order=1),
        _mod("line_items", "$.lineItems[*]",
             {"type": "object", "properties": {
                 "description": {"type": "string"},
                 "quantity": {"type": "number"},
             }}, order=2),
    ]


# ── 紧凑 Schema 树 ──────────────────────────────────────────────────────────

def test_schema_block_is_compact_tree_not_json_dump():
    text = composer.assemble_prompt(_two_modules(), country_global=None)
    assert "# 整体输出 Schema" in text  # header 兼容（导航/测试定位依赖）
    # 不再有 indent=2 的全量 JSON dump
    schema = composer.assemble_schema(_two_modules())
    assert json.dumps(schema, ensure_ascii=False, indent=2) not in text
    # 树行存在：标量字段 + 数组字段 + 子字段
    assert "- invoiceNumber: string" in text
    assert "- lineItems[]: object" in text
    assert "- quantity: number" in text
    # 提示完整约束在 response_schema
    assert "response_schema" in text


def test_schema_tree_renders_enum_inline():
    mods = [_mod("currency", "$.currency",
                 {"type": "string", "enum": ["MYR", "USD"]}, order=1)]
    text = composer.assemble_prompt(mods, country_global=None)
    assert "enum: MYR | USD" in text


def test_schema_tree_size_reduction_on_wide_schema():
    # 仿真 30 字段宽表：树渲染必须显著小于 indent=2 dump
    mods = [
        _mod(f"field_{i}", f"$.field{i}", {"type": "string"}, order=i)
        for i in range(30)
    ]
    schema = composer.assemble_schema(mods)
    dump = json.dumps(schema, ensure_ascii=False, indent=2)
    tree = composer._render_schema_tree(schema)
    assert len(tree) < len(dump) * 0.5  # 至少省一半（实测 ~80%）


def test_schema_tree_fallback_on_exotic_schema():
    # 异常结构（无 properties 无 type）→ 回退紧凑 JSON，不抛异常
    out = composer._render_schema_tree({"weird": True})
    assert "weird" in out


# ── 客户反馈折叠（确定性，composer 内零 LLM）─────────────────────────────────

def test_single_feedback_block_unchanged():
    p = "基础规则\n\n# 客户反馈补充\n- 去掉 RM 前缀"
    assert composer._fold_feedback_blocks(p) == p


def test_duplicate_feedback_blocks_fold_and_dedup():
    p = (
        "基础规则\n\n"
        "# 客户反馈补充\n- 去掉 RM 前缀\n- 保留两位小数\n\n"
        "# 客户反馈补充\n- 去掉 RM 前缀\n- 数字不要千分位"
    )
    folded = composer._fold_feedback_blocks(p)
    assert folded.count("# 客户反馈补充") == 1
    assert folded.count("去掉 RM 前缀") == 1       # 重复行去重
    assert "保留两位小数" in folded                 # 不同行都保留
    assert "数字不要千分位" in folded
    assert "已合并 2 块" in folded
    assert folded.startswith("基础规则")            # 基体不动


def test_module_body_render_folds_via_assemble():
    m = _mod(
        "amount", "$.amount", {"type": "number"},
        prompt=(
            "取右下角总金额\n\n"
            "# 客户反馈补充\n- 去千分位\n\n"
            "# 客户反馈补充\n- 去千分位\n- 负数保留减号"
        ),
        order=1,
    )
    text = composer.assemble_prompt([m], country_global=None)
    # 折叠后模块体内只剩一个反馈块、去千分位只出现一次
    body = text[text.index("# 模块识别指令"):]
    assert body.count("# 客户反馈补充") == 1
    assert body.count("去千分位") == 1
    assert "负数保留减号" in body


# ── reconciler 膨胀阈值（统筹触发条件）──────────────────────────────────────

def test_is_bloated_thresholds():
    from app.ocr_optimizer.service import reconciler

    assert not reconciler.is_bloated(None)
    assert not reconciler.is_bloated("简短规则")
    # 批次6：阈值 2 块 → 3 块、600 → 1500 字符——原阈值让健康 prompt
    # 每轮被 LLM 有损重写（不可复现漂移）
    two_blocks = "x\n# 客户反馈补充\na\n# 客户反馈补充\nb"
    assert not reconciler.is_bloated(two_blocks)
    three_blocks = two_blocks + "\n# 客户反馈补充\nc"
    assert reconciler.is_bloated(three_blocks)
    assert reconciler.is_bloated("y" * 1501)
    assert not reconciler.is_bloated("y" * 1500)
