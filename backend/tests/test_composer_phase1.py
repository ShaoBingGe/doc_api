"""
Prompt System v2 — Phase 1 structural tests.

Locks the navigation block + per-module identity framing, and re-asserts that
the §① render order is UNCHANGED (navigation slots between preamble and the
country section; Part 3 content untouched).
"""

from __future__ import annotations

from types import SimpleNamespace


def _module(key, prompt, json_path="$[*].x", display=None, ftype="string"):
    return SimpleNamespace(
        module_key=key,
        display_name=display or key,
        json_path=json_path,
        schema_fragment={"type": ftype},
        ocr_prompt=prompt,
        order_index=0,
    )


def test_navigation_block_present_and_first():
    from app.ocr_optimizer.service import composer
    text = composer.assemble_prompt([_module("foo", "find foo")], country_global="CG")
    assert "# 阅读导航" in text
    # Navigation sits after the preamble and before the country section.
    p_preamble = text.index("你是一名严谨的文档信息抽取专家")
    p_nav = text.index("# 阅读导航")
    p_country = text.index("CG")
    assert p_preamble < p_nav < p_country


def test_navigation_does_not_shadow_section_headers():
    """The nav must not contain the exact header strings later code/tests
    locate by first occurrence."""
    from app.ocr_optimizer.service import composer
    nav = composer.GLOBAL_NAVIGATION
    for marker in (
        "# 整体输出 Schema",
        "# Part 3 · 输出契约与装配规则",
        "# 模块识别指令",
        "# 输出前自检",
    ):
        assert marker not in nav, f"nav must not contain exact header {marker!r}"


def test_module_identity_line_rendered():
    from app.ocr_optimizer.service import composer
    text = composer.assemble_prompt(
        [_module("bill_from_name", "找开票方", json_path="$[*].billFromName",
                 display="开票方名称", ftype="string")],
        country_global="CG",
    )
    assert "## 1. 开票方名称" in text          # header keeps the "## N. name" shape
    assert "字段键 `bill_from_name`" in text
    assert "输出路径 `$[*].billFromName`" in text
    assert "类型 string" in text


def test_render_order_unchanged():
    """§① order must still hold with navigation + intro inserted."""
    from app.ocr_optimizer.service import composer
    text = composer.assemble_prompt([_module("foo", "find foo")], country_global="MARKER")
    p_preamble = text.index("你是一名严谨的文档信息抽取专家")
    p_country = text.index("MARKER")
    p_schema = text.index("# 整体输出 Schema")
    p_part3 = text.index("# Part 3 · 输出契约与装配规则")
    p_modules = text.index("# 模块识别指令")
    p_selfcheck = text.index("# 输出前自检")
    assert p_preamble < p_country < p_schema < p_part3 < p_modules < p_selfcheck
    assert "## 1. foo" in text
