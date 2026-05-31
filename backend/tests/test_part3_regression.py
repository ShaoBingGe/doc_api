"""
Offline structural regression for design v7 (Part 3 promotion).

What this validates without calling Gemini:
  - The new MY yaml still emits 29 modules (same field set as design v6)
  - Schema descriptions absorbed the recognition rules from old Part 2.2:
    * `totalTaxAmount` retains 语义优先 / 结构识别 / 严禁推算
    * `detailOfTaxSummary[].tax` mentions semantic-priority + 严禁推算
    * `detailOfGoodsOrServices[].quantity` mentions 单位列辨识
  - Part 3 rules from the platform asset reach the v1 composed_prompt:
    * §3.2 numeric normalization
    * §3.3 ADJUSTMENT 平账
    * §3.4 行项目装配 (< 0.01) + PO/SO/DO 头/行二选一
    * §3.5 跨页装配
    * §3.6 Credit Note + originalInvoiceReferences
    * §3.7 缺失字段处理
  - country_global_text (used by fork/round inheritance) contains Part 1 + Part 2
    but NOT Part 3 (otherwise composer would double-inject)
  - GT field names from the 2 reviewed CHINKIN samples all map to a schema field.

Run:  pytest tests/test_part3_regression.py -v
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


def _decompose():
    from app.ocr_optimizer.service.template_loader import decompose_country_template
    return decompose_country_template("MY")


def test_module_count_unchanged():
    d = _decompose()
    assert len(d["modules"]) == 29, f"expected 29 modules, got {len(d['modules'])}"


def test_country_global_text_excludes_part3():
    d = _decompose()
    text = d["country_global_text"] or ""
    assert "# Part 1" in text
    assert "# Part 2" in text
    assert "# Part 3" not in text, "country_global_text must end before # Part 3"
    # Verify Part 1.2 currency input-side rule retained
    assert "千分位" in text and "MYR" in text


def test_schema_descriptions_absorbed_2_2_rules():
    """The recognition-side content from old Part 2.2 must live in field
    descriptions so the LLM sees it per-field."""
    from app.ocr_optimizer.service import template_loader
    raw = template_loader.load_country_template("MY")
    invoice_branch = raw["prompt_template"]["json_schema"]["items"]["anyOf"][0]
    props = invoice_branch["properties"]

    total_tax_desc = props["totalTaxAmount"]["description"]
    assert "语义优先" in total_tax_desc
    assert "结构识别" in total_tax_desc
    assert "严禁推算" in total_tax_desc

    tax_summary_items = props["detailOfTaxSummary"]["items"]["properties"]
    tax_desc = tax_summary_items["tax"]["description"]
    assert "语义优先" in tax_desc
    assert "严禁推算" in tax_desc

    line_items = props["detailOfGoodsOrServices"]["items"]["properties"]
    qty_desc = line_items["quantity"]["description"]
    assert "单位列辨识" in qty_desc
    assert "PCS" in qty_desc  # the multi-unit hint


def test_part3_rules_reach_v1_composed_prompt():
    """Simulate what preset_init does for v1 and assert Part 3 rules land."""
    from app.ocr_optimizer.service.composer import GLOBAL_OUTPUT_CONTRACT_DETAILS
    d = _decompose()
    v1 = d["prompt_format"].rstrip() + "\n\n" + GLOBAL_OUTPUT_CONTRACT_DETAILS

    # All 8 sections present
    for sec in ("3.1 顶层结构", "3.2 数值字段统一规范", "3.3 税额装配",
                "3.4 行项目装配", "3.5 跨页装配", "3.6 Credit Note 装配",
                "3.7 缺失字段处理", "3.8 字段输出顺序"):
        assert sec in v1, f"missing section: {sec}"

    # User-locked specifics
    assert "< 0.01" in v1, "1% threshold missing from §3.4"
    assert "ADJUSTMENT" in v1, "ADJUSTMENT 平账 missing from §3.3"
    assert "originalInvoiceReferences" in v1, "§3.6 must mention the field"


def test_v2_plus_composer_chain():
    """Verify composer.assemble_prompt produces well-ordered prompts using
    only country_global_text (which is what fork/round will inherit)."""
    from app.ocr_optimizer.service import composer
    from types import SimpleNamespace

    d = _decompose()
    # Build SimpleNamespace stand-ins for OcrModule (composer only reads
    # attributes, not the SQLAlchemy mapping)
    mods = [
        SimpleNamespace(
            module_key=m["module_key"],
            display_name=m["display_name"],
            json_path=m["json_path"],
            schema_fragment=m["schema_fragment"],
            ocr_prompt=m["ocr_prompt"],
            order_index=m["order_index"],
        )
        for m in d["modules"]
    ]
    text = composer.assemble_prompt(mods, country_global=d["country_global_text"])

    # Render order:
    p_preamble = text.index("你是一名严谨的文档信息抽取专家")
    p_country = text.index("# Part 1")
    p_schema = text.index("# 整体输出 Schema")
    p_part3 = text.index("# Part 3 · 输出契约与装配规则")
    p_modules = text.index("# 模块识别指令")
    p_selfcheck = text.index("# 输出前自检")
    assert p_preamble < p_country < p_schema < p_part3 < p_modules < p_selfcheck

    # Part 3 appears exactly ONCE in fork/round chain (no v1 yaml-Part-3 duplication)
    assert text.count("# Part 3 · 输出契约与装配规则") == 1


_VALID_FIELD_NAME = __import__("re").compile(r"^[a-z][a-zA-Z0-9_]*$")


def _load_gt(path: Path) -> dict[str, str]:
    """Load TSV but ignore continuation rows whose first column is not a
    valid camelCase field name (multi-line address values split across rows).
    """
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    out: dict[str, str] = {}
    for r in rows:
        name = (r.get("field_name") or "").strip()
        if not _VALID_FIELD_NAME.match(name):
            continue
        out[name] = r.get("field_value") or ""
    return out


def test_gt_fields_all_map_to_schema():
    """Every GT field name from the 2 reviewed CHINKIN samples must
    resolve to an OcrModule (top-level field or nested via json_path)."""
    panasonic = Path("/tmp/gt_panasonic.tsv")
    rental = Path("/tmp/gt_rental.tsv")
    if not panasonic.exists() or not rental.exists():
        import pytest
        pytest.skip("GT files not present (run the recon TSV dump first)")

    gt_fields = set(_load_gt(panasonic).keys()) | set(_load_gt(rental).keys())

    d = _decompose()
    # Top-level invoice branch property names
    raw = json.loads(json.dumps(
        d["json_schema"]["items"]["anyOf"][0]["properties"]
    ))
    schema_top_level = set(raw.keys())
    # Plus nested keys from array items
    nested = set()
    for k, v in raw.items():
        if v.get("type", "").upper() == "ARRAY":
            for nk in (v.get("items", {}).get("properties") or {}).keys():
                nested.add(f"{k}.{nk}")
                nested.add(nk)  # the GT may flatten array keys

    missing = gt_fields - schema_top_level - nested
    # GT may include array-row indexed fields like "detailOfGoodsOrServices[0].quantity"
    # — accept those by stripping bracket suffix
    unresolved = []
    for f in missing:
        base = f.split("[")[0]
        if base not in schema_top_level and base not in nested:
            # try cross-array path "detailOfGoodsOrServices.quantity"
            if "." in f:
                head, tail = f.split(".", 1)
                if head in schema_top_level and tail.split("[")[0] in nested:
                    continue
            unresolved.append(f)
    assert not unresolved, f"GT fields without schema mapping: {unresolved}"
