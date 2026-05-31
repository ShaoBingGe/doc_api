"""
Composer Part 3 (GLOBAL_OUTPUT_CONTRACT_DETAILS) tests.

Verifies design v7 contract:
  - Part 3 is loaded from the platform yaml asset
  - composer.assemble_prompt injects it AFTER the schema reference and
    BEFORE the per-module bodies
  - country_global (Part 1) injects BEFORE the schema reference
  - Render order is stable: PREAMBLE → Part 1 → schema → Part 3 → modules → self-check
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _module(key: str, prompt: str, json_path: str = "$.x") -> SimpleNamespace:
    return SimpleNamespace(
        module_key=key,
        display_name=key,
        json_path=json_path,
        schema_fragment={"type": "string"},
        ocr_prompt=prompt,
        order_index=0,
    )


def test_output_contract_loads():
    from app.ocr_optimizer.service.output_contract import (
        get_tax_category_standard_abbreviations,
        render_output_contract,
    )
    text = render_output_contract()
    assert "# Part 3 · 输出契约与装配规则" in text
    for section in ("3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8"):
        assert f"## {section} " in text, f"missing {section}"
    # 1% threshold for line-item check
    assert "< 0.01" in text
    # ADJUSTMENT row pattern
    assert "ADJUSTMENT" in text
    cats = get_tax_category_standard_abbreviations()
    assert "VAT" in cats and "SST" in cats and "ADJUSTMENT" in cats


def test_assemble_prompt_render_order():
    from app.ocr_optimizer.service import composer
    mods = [_module("foo", "find foo")]
    text = composer.assemble_prompt(
        mods, country_global="MY_COUNTRY_MARKER"
    )

    # All four anchors present
    assert composer.GLOBAL_PREAMBLE.split("\n")[0] in text
    assert "MY_COUNTRY_MARKER" in text
    assert "# 整体输出 Schema" in text
    assert "# Part 3 · 输出契约与装配规则" in text
    assert "# 模块识别指令" in text
    assert "## 1. foo" in text
    # Self-check tail
    assert "# 输出前自检" in text

    # Strict ordering
    p_preamble = text.index("你是一名严谨的文档信息抽取专家")
    p_country = text.index("MY_COUNTRY_MARKER")
    p_schema = text.index("# 整体输出 Schema")
    p_part3 = text.index("# Part 3 · 输出契约与装配规则")
    p_modules = text.index("# 模块识别指令")
    p_selfcheck = text.index("# 输出前自检")
    assert p_preamble < p_country < p_schema < p_part3 < p_modules < p_selfcheck


def test_assemble_prompt_without_country_global():
    """country_global=None must still produce a well-formed prompt with Part 3."""
    from app.ocr_optimizer.service import composer
    mods = [_module("bar", "find bar")]
    text = composer.assemble_prompt(mods, country_global=None)
    # Part 3 still injected even without country section
    assert "# Part 3 · 输出契约与装配规则" in text
    assert "# 整体输出 Schema" in text
    p_schema = text.index("# 整体输出 Schema")
    p_part3 = text.index("# Part 3 · 输出契约与装配规则")
    assert p_schema < p_part3


def test_assemble_prompt_keyword_only_country_global():
    """country_global is keyword-only; positional must TypeError."""
    from app.ocr_optimizer.service import composer
    mods = [_module("baz", "find baz")]
    with pytest.raises(TypeError):
        composer.assemble_prompt(mods, "positional country")  # type: ignore[misc]
