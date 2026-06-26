"""P3 慢/元更新 纯函数单测 —— SKT-S（slow_update）+ SKT-M3（meta_skill）。"""
from app.ocr_optimizer.skilltrain.slow_update import (
    GUARDIAN_BLOCK_HEADER,
    Guardian,
    compute_guardians,
    render_guardian_block,
)
from app.ocr_optimizer.skilltrain.meta_skill import (
    render_meta_hint,
    summarize_edit_outcomes,
)
from app.ocr_optimizer.skilltrain.types import FieldEdit


# ── slow_update ────────────────────────────────────────────────────────────


def test_pin_when_final_meets_target():
    g = compute_guardians({"invoiceNumber": [0.8, 1.0, 1.0]})
    assert len(g) == 1
    assert g[0].kind == "pin" and g[0].field == "invoiceNumber"


def test_caution_when_regressed_from_peak():
    g = compute_guardians({"currency": [0.5, 1.0, 0.5]})
    assert len(g) == 1
    assert g[0].kind == "caution"
    assert "峰值 100%" in g[0].note and "末 50%" in g[0].note


def test_caution_when_volatile_swing():
    # ends below target, big swing, but did not regress from peak at the very end
    g = compute_guardians({"buyer": [0.2, 0.6, 0.6]})  # swing 0.4 >= 0.34
    assert g and g[0].kind == "caution"


def test_no_guardian_for_steady_improvement_below_target():
    # monotone small improvement, low swing, below target → no guardian
    g = compute_guardians({"total": [0.60, 0.63, 0.66]})
    assert g == []


def test_single_point_trajectory_skipped():
    assert compute_guardians({"x": [1.0]}) == []


def test_guardians_sorted_by_field():
    g = compute_guardians({"zeta": [1.0, 1.0], "alpha": [1.0, 1.0]})
    assert [x.field for x in g] == ["alpha", "zeta"]


def test_render_guardian_block_empty_when_none():
    assert render_guardian_block([]) == ""


def test_render_guardian_block_has_header_and_lines():
    block = render_guardian_block([Guardian("f", "pin", "保持规则")])
    assert block.startswith(GUARDIAN_BLOCK_HEADER)
    assert "- 保持规则" in block


# ── meta_skill ─────────────────────────────────────────────────────────────


def _e(op):
    return FieldEdit(op=op, target="f")


def test_summarize_counts_and_reject_rate():
    s = summarize_edit_outcomes(
        accepted=[_e("append"), _e("append")],
        rejected=[_e("replace"), _e("replace"), _e("replace")],
    )
    assert s["total_accepted"] == 2 and s["total_rejected"] == 3
    assert s["by_op"]["replace"]["reject_rate"] == 1.0
    assert s["by_op"]["append"]["reject_rate"] == 0.0


def test_meta_hint_flags_high_reject_op():
    s = summarize_edit_outcomes(
        accepted=[_e("append")],
        rejected=[_e("replace"), _e("replace"), _e("replace")],
    )
    hint = render_meta_hint(s)
    assert "replace" in hint and "%" in hint


def test_meta_hint_empty_when_below_min_samples():
    # replace rejected twice (<3 total) → not enough evidence
    s = summarize_edit_outcomes(accepted=[], rejected=[_e("replace"), _e("replace")])
    assert render_meta_hint(s) == ""


def test_meta_hint_empty_when_reject_rate_low():
    s = summarize_edit_outcomes(
        accepted=[_e("append"), _e("append"), _e("append")],
        rejected=[_e("append")],
    )
    assert render_meta_hint(s) == ""


# ── composer guardian_block wiring (P3) ─────────────────────────────────────


def _mod(key, prompt):
    from types import SimpleNamespace

    return SimpleNamespace(
        module_key=key,
        display_name=key,
        json_path="$." + key,
        schema_fragment={"type": "string"},
        ocr_prompt=prompt,
        order_index=0,
    )


def test_guardian_block_rendered_before_modules():
    from app.ocr_optimizer.service import composer

    block = "# 守护指引（slow-update · 请勿改写本段规则）\n- 字段「foo」已稳定达标。"
    text = composer.assemble_prompt([_mod("foo", "find foo")], country_global=None, guardian_block=block)
    assert "守护指引" in text
    # protected block sits between the modules header and the first module body
    assert text.index("# 模块识别指令") < text.index("守护指引") < text.index("## 1. foo")


def test_guardian_block_none_is_unchanged():
    from app.ocr_optimizer.service import composer

    mods = [_mod("bar", "find bar")]
    base = composer.assemble_prompt(mods, country_global=None)
    with_none = composer.assemble_prompt(mods, country_global=None, guardian_block=None)
    assert base == with_none  # default OFF → byte-identical
