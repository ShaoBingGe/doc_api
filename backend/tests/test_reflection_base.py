"""
Phase 5 — shared reflection base (公共基底 + 薄变体).

The doctrine + edit output schema live once under reflection/base/ and are
injected into each skill/agent render via {base_doctrine} / {base_edit_output}.
"""

from __future__ import annotations


def test_base_assets_load():
    from app.ocr_optimizer.reflection.base_assets import base_format_vars
    v = base_format_vars()
    assert "相对锚点" in v["base_doctrine"]          # generalization doctrine
    assert "holds_for_all" in v["base_doctrine"]
    assert "fix_suggestion" in v["base_edit_output"]  # structured output schema
    assert "generalization" in v["base_edit_output"]


def test_skill_render_injects_base():
    from app.ocr_optimizer.reflection.skills_loader import load_skills
    skills = {s.key: s for s in load_skills(force=True)}
    diff = {"kind": "edit", "module_key": "invoice_number",
            "original_name": "invoiceNumber", "corrected_name": "invoiceNumber",
            "original_value": "WRONG", "corrected_value": "INV-1"}
    rendered = skills["value_mismatch"].render(diff, {"description": "发票号", "ocr_prompt": "找发票号"})
    # the thin skill no longer inlines the doctrine/output, but the rendered
    # prompt MUST contain them (from base) + no leftover {placeholders}
    assert "相对锚点" in rendered
    assert "fix_suggestion" in rendered
    assert "generalization" in rendered
    assert "{base_doctrine}" not in rendered      # placeholder was substituted
    assert "{base_edit_output}" not in rendered
    assert "INV-1" in rendered                    # field substitution still works


def test_country_agent_render_injects_base():
    from app.ocr_optimizer.reflection.country_agents_loader import load_country_agents
    agents = load_country_agents("MY")
    diff = {"kind": "edit", "module_key": "saler_name",
            "original_name": "salerName", "corrected_name": "salerName",
            "original_value": "X", "corrected_value": "DAY BRIGHT"}
    rendered = agents["edit"].render(diff, {"description": "卖方", "ocr_prompt": "找卖方"})
    assert "相对锚点" in rendered                  # base_doctrine injected
    assert "fix_suggestion" in rendered            # base_edit_output injected
    assert "{base_doctrine}" not in rendered
