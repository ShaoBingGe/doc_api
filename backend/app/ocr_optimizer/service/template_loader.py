"""
Country-template YAML loader & decomposer.

Reads a `<COUNTRY>_invoice_prompt.yaml` from the repo root and produces
the 30-module decomposition described in docs/ocr-optimizer-design.md §6.4.

The yaml files are READ-ONLY (see design §15 constraint #13). This module
never writes to them.
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Repo root: backend/app/ocr_optimizer/service/template_loader.py
#            └─ parents[4] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Default replacement for the `{tax_categories_text}` placeholder in
# yaml.prompt_format. Per design §6.4 / Q tax_categories.
TAX_CATEGORIES_DEFAULT = "请使用文档中出现的原名"

# Markers that delimit the global-rules section in yaml.prompt_format.
# Everything from the FIRST start marker (inclusive) up to either the END
# marker (exclusive) or end-of-string is captured as country_global_text.
#
# START markers in priority order:
#   1. "# Part 1"        — design v5+ country template (Part 1 = country
#                          global rules; Part 2 = per-field rules; Part 3 =
#                          delegated to platform output contract)
#   2. "**提取规则：**" — legacy marker used by pre-v5 country YAMLs
#
# END marker (design v7):
#   "# Part 3"           — Part 3 is platform-owned (loaded by composer
#                          from assets/global_output_contract.yaml at
#                          runtime). Excluded from country_global_text to
#                          avoid double-injection.
_GLOBAL_RULES_START_MARKERS = ("# Part 1", "**提取规则：**")
_GLOBAL_RULES_END_MARKER = "# Part 3"

# Backwards-compatible alias (some external code may import this name).
_GLOBAL_RULES_MARKERS = _GLOBAL_RULES_START_MARKERS


# ── Public API ───────────────────────────────────────────────────────────────

def list_available_countries() -> list[dict[str, Any]]:
    """Scan the repo root for `<COUNTRY>_invoice_prompt.yaml` files.

    Returns a list of `{country: str, available: bool}` for the hardcoded
    chip list defined in UI_DESIGN §14.2: MY, CN, US, EU, GLOBAL.
    """
    chips = ["MY", "JP", "CN", "US", "EU", "GLOBAL"]
    found: set[str] = set()
    for p in _REPO_ROOT.glob("*_invoice_prompt.yaml"):
        # filename e.g. "MY_invoice_prompt.yaml" → "MY"
        prefix = p.name.split("_", 1)[0]
        if prefix:
            found.add(prefix.upper())
    return [{"country": c, "available": c in found} for c in chips]


def locked_fields_for(country: str) -> set[str]:
    """Return the set of country-regulated, NON-modifiable field names declared
    in `<COUNTRY>_invoice_prompt.yaml` under the top-level `locked_fields:` list.

    Locked fields are governed by the country spec (Part 1): their recognition
    rule is pinned, they are excluded from Part-2 reflection/optimization, and
    the customer cannot add/delete/rename/retype them. Empty set when the yaml
    is missing or declares none. Read at runtime (no DB column) so a change to
    the country policy applies to every API of that country immediately.
    """
    try:
        data = load_country_template(country)
    except (FileNotFoundError, Exception):  # noqa: BLE001 — governance read is best-effort
        return set()
    raw = data.get("locked_fields") or []
    return {str(x) for x in raw if x}


def load_country_template(country: str) -> dict[str, Any]:
    """Load `<COUNTRY>_invoice_prompt.yaml`, return parsed dict.

    Raises FileNotFoundError if the yaml is not present.
    """
    path = _REPO_ROOT / f"{country.upper()}_invoice_prompt.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Country template not available: {path.name} not found in repo root"
        )
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def decompose_country_template(country: str) -> dict[str, Any]:
    """Parse a country yaml and produce the §6.4 decomposition.

    Returns:
        {
            "country": "MY",
            "yaml_id": "7_6",                       # yaml's `id` field
            "prompt_format": <full rendered text>,  # with placeholders replaced
            "json_schema": <yaml's items.anyOf[0]>, # the invoice/receipt branch only
            "modules": [
                {
                    "module_key": "global_rules",
                    "display_name": "全局规则与约束",
                    "json_path": "$",
                    "schema_fragment": {},
                    "ocr_suggestions": {...},
                    "ocr_prompt": "<extracted global rules text>",
                    "order_index": 0,
                    "description": "...",
                },
                {
                    "module_key": "doc_type",
                    "display_name": "票据大类识别",
                    "json_path": "$[*].docType",
                    ...
                    "order_index": 1,
                },
                ... 28 more entries (26 scalars + 3 arrays + 1 global_rules = 30 total)
            ]
        }
    """
    data = load_country_template(country)
    tpl = data.get("prompt_template") or {}
    raw_prompt = tpl.get("prompt_format")
    raw_schema = tpl.get("json_schema")
    if not raw_prompt or not raw_schema:
        raise ValueError(
            f"Invalid yaml: {country}_invoice_prompt.yaml must contain "
            f"prompt_template.prompt_format and prompt_template.json_schema"
        )

    rendered_prompt = raw_prompt.replace("{tax_categories_text}", TAX_CATEGORIES_DEFAULT)
    invoice_branch = _extract_invoice_branch(raw_schema)

    modules: list[dict[str, Any]] = []
    country_global_text = _extract_global_rules_text(rendered_prompt)

    props = invoice_branch.get("properties") or {}
    order_index = 1
    array_modules: list[dict[str, Any]] = []
    for field_name, field_schema in props.items():
        ftype = (field_schema.get("type") or "").upper()
        if ftype == "ARRAY":
            # Defer arrays so they get the trailing order_index slots
            array_modules.append(
                _build_array_module(field_name, field_schema, order_index=0)
            )
        else:
            modules.append(
                _build_scalar_module(field_name, field_schema, order_index=order_index)
            )
            order_index += 1
    for arr_mod in array_modules:
        arr_mod["order_index"] = order_index
        modules.append(arr_mod)
        order_index += 1

    # Mark country-locked modules so downstream (reflection-exclude, override-
    # refuse, UI-lock) can identify them. Locked-ness is keyed by the schema
    # field name (json_path leaf), matching `locked_fields` in the yaml.
    locked = {str(x) for x in (data.get("locked_fields") or []) if x}
    if locked:
        for mod in modules:
            leaf = (mod.get("json_path") or "").split(".")[-1]
            leaf = leaf.replace("[*]", "").replace("[", "").replace("]", "").strip()
            mod["locked"] = leaf in locked

    return {
        "country": country.upper(),
        "yaml_id": data.get("id"),
        "prompt_format": rendered_prompt,
        "json_schema": raw_schema,  # store original (with anyOf intact)
        "country_global_text": country_global_text,
        "modules": modules,
        "locked_fields": sorted(locked),
    }


# ── Internals ────────────────────────────────────────────────────────────────


def _extract_invoice_branch(raw_schema: dict) -> dict:
    """Pull the invoice/receipt branch (anyOf[0]) of the yaml schema.

    Expected shape per MY_invoice_prompt.yaml:
        { "type": "ARRAY", "items": { "anyOf": [<invoice branch>, <other branch>] } }
    """
    items = raw_schema.get("items") or {}
    any_of = items.get("anyOf") or []
    if not any_of:
        raise ValueError("yaml.json_schema.items.anyOf is empty or missing")
    return any_of[0]


def _extract_global_rules_text(rendered_prompt: str) -> str:
    """Extract the country-global-rules section from prompt_format.

    Replaces the legacy `_build_global_rules_module` — per design v6 the
    rules live in `OcrPromptVersion.country_global_text` instead of being
    wrapped as a fake module.

    Design v7 update:
      - START: from the FIRST matched start marker (inclusive)
      - END:   stops at "# Part 3" (exclusive) if present, otherwise EOF
      - Reason: Part 3 (output contract) is platform-owned and injected by
        composer from a yaml asset; country yaml's Part 3 is a human-readable
        reference only and must NOT be persisted to country_global_text
        (otherwise composed_prompt would contain Part 3 twice).

    Falls back to the entire prompt when no start marker is found (lenient —
    better than failing init).
    """
    start_idx = -1
    matched_start: str | None = None
    for marker in _GLOBAL_RULES_START_MARKERS:
        idx = rendered_prompt.find(marker)
        if idx >= 0:
            start_idx = idx
            matched_start = marker
            break

    if start_idx < 0:
        logger.warning(
            "No global-rules start marker found in yaml.prompt_format (tried %s); "
            "storing entire prompt as country_global_text",
            list(_GLOBAL_RULES_START_MARKERS),
        )
        return rendered_prompt

    end_idx = rendered_prompt.find(_GLOBAL_RULES_END_MARKER, start_idx)
    if end_idx > start_idx:
        rules_text = rendered_prompt[start_idx:end_idx].rstrip()
        logger.info(
            "country_global_text captured from %r to %r (length=%d)",
            matched_start, _GLOBAL_RULES_END_MARKER, len(rules_text),
        )
    else:
        rules_text = rendered_prompt[start_idx:]
        logger.info(
            "country_global_text captured from %r to EOF (no %r marker; length=%d)",
            matched_start, _GLOBAL_RULES_END_MARKER, len(rules_text),
        )
    return rules_text


def _snake(camel: str) -> str:
    """invoiceNumber → invoice_number, etc. (also lowercases all-caps tokens)."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", camel)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


_DISPLAY_OVERRIDES: dict[str, str] = {
    "docType": "票据大类识别",
    "invoiceType": "发票子类型识别",
    "nameOfInvoice": "票面标题识别",
    "invoiceNumber": "发票号码识别",
    "invoiceCode": "发票代码/序列号识别",
    "invoiceDate": "发票日期识别",
    "dueDate": "付款截止日期识别",
    "purchaseOrderNumber": "采购订单号识别",
    "salesOrderNumber": "销售订单号识别",
    "deliveryOrderNumber": "发货单号识别",
    "currency": "币种识别",
    "totalNetAmount": "不含税总净额识别",
    "totalAmount": "含税总金额识别",
    "totalTaxAmount": "总税额识别",
    "billToName": "收票方名称识别",
    "billToComposite": "收票方完整地址识别",
    "billToCountry": "收票方国家识别",
    "billToCountryCode": "收票方国家代码识别",
    "billToTaxIdentificationNumber": "收票方税号识别",
    "billFromName": "开票方名称识别",
    "billFromComposite": "开票方完整地址识别",
    "billFromCountry": "开票方国家识别",
    "billFromCountryCode": "开票方国家代码识别",
    "billFromTaxIdentificationNumber": "开票方税号识别",
    "billFromBusinessRegistrationNumber": "开票方商业登记号识别",
    "page": "页码识别",
    "detailOfGoodsOrServices": "商品/服务明细识别",
    "detailOfTaxSummary": "税金汇总识别",
    "originalInvoiceReferences": "原始发票引用识别",
}


def _display_name(field_name: str) -> str:
    return _DISPLAY_OVERRIDES.get(field_name, f"{field_name}识别")


def _build_scalar_module(
    field_name: str, field_schema: dict, *, order_index: int
) -> dict[str, Any]:
    module_key = _snake(field_name)
    display_name = _display_name(field_name)
    json_path = f"$[*].{field_name}"
    desc_text = (field_schema.get("description") or "").strip()
    type_str = (field_schema.get("type") or "STRING").upper()
    enum_vals = field_schema.get("enum")

    type_clause = f"该字段类型：{type_str}"
    if enum_vals:
        type_clause += f"（枚举：{', '.join(enum_vals)}）"

    is_number = type_str in {"NUMBER", "INTEGER"}
    extra_rule = (
        "金额一律输出纯数字，遵循 global_rules 中的千分位与小数点规则。"
        if is_number
        else ""
    )

    ocr_prompt = (
        f"你负责从文档中识别「{display_name}」字段。\n\n"
        f"输出位置（json_path）：{json_path}\n"
        f"{type_clause}\n\n"
        f"# 识别规则\n"
        f"{desc_text}\n\n"
        f"# 输出要求\n"
        f"找不到时输出 null。{extra_rule}"
    )

    return {
        "module_key": module_key,
        "display_name": display_name,
        "json_path": json_path,
        "schema_fragment": copy.deepcopy(field_schema),
        "ocr_suggestions": {
            "semantics": "待优化器自动学习",
            "position": "待优化器自动学习",
            "most_common_feature": "待优化器自动学习",
            "extra_features": [],
        },
        "ocr_prompt": ocr_prompt,
        "description": desc_text or f"识别票据中的 {field_name} 字段",
        "order_index": order_index,
    }


_ARRAY_MODULE_KEY_OVERRIDES = {
    "detailOfGoodsOrServices": "line_items",
    "detailOfTaxSummary": "tax_summary",
    "originalInvoiceReferences": "original_invoice_references",
}


def _build_array_module(
    field_name: str, field_schema: dict, *, order_index: int
) -> dict[str, Any]:
    module_key = _ARRAY_MODULE_KEY_OVERRIDES.get(field_name, _snake(field_name))
    display_name = _display_name(field_name)
    json_path = f"$[*].{field_name}[*]"
    desc_text = (field_schema.get("description") or "").strip()

    items_schema = field_schema.get("items") or {"type": "OBJECT"}
    item_props = (items_schema.get("properties") or {}) if isinstance(items_schema, dict) else {}
    column_list = ", ".join(item_props.keys()) if item_props else "—"

    ocr_prompt = (
        f"你负责从文档中识别「{display_name}」（数组类字段）。\n\n"
        f"输出位置（json_path）：{json_path}\n"
        f"该字段类型：ARRAY[OBJECT]\n\n"
        f"# 识别规则\n"
        f"{desc_text}\n\n"
        f"# 输出形式\n"
        f"JSON 数组，每行一个对象，含字段：{column_list}\n\n"
        f"# 输出要求\n"
        f"找不到对应行时输出空数组 []。"
    )

    return {
        "module_key": module_key,
        "display_name": display_name,
        "json_path": json_path,
        "schema_fragment": copy.deepcopy(items_schema),
        "ocr_suggestions": {
            "semantics": "待优化器自动学习",
            "position": "待优化器自动学习",
            "most_common_feature": "待优化器自动学习",
            "extra_features": [],
        },
        "ocr_prompt": ocr_prompt,
        "description": desc_text or f"识别票据中的 {field_name} 数组",
        "order_index": order_index,
    }
