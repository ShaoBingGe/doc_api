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

# Marker that delimits the global-rules section in yaml.prompt_format.
# Everything from this marker (inclusive) to the end of prompt_format is
# considered global rules; everything before is opening narrative.
_GLOBAL_RULES_MARKER = "**提取规则：**"


# ── Public API ───────────────────────────────────────────────────────────────

def list_available_countries() -> list[dict[str, Any]]:
    """Scan the repo root for `<COUNTRY>_invoice_prompt.yaml` files.

    Returns a list of `{country: str, available: bool}` for the hardcoded
    chip list defined in UI_DESIGN §14.2: MY, CN, US, EU, GLOBAL.
    """
    chips = ["MY", "CN", "US", "EU", "GLOBAL"]
    found: set[str] = set()
    for p in _REPO_ROOT.glob("*_invoice_prompt.yaml"):
        # filename e.g. "MY_invoice_prompt.yaml" → "MY"
        prefix = p.name.split("_", 1)[0]
        if prefix:
            found.add(prefix.upper())
    return [{"country": c, "available": c in found} for c in chips]


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
    modules.append(_build_global_rules_module(rendered_prompt))

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

    return {
        "country": country.upper(),
        "yaml_id": data.get("id"),
        "prompt_format": rendered_prompt,
        "json_schema": raw_schema,  # store original (with anyOf intact)
        "modules": modules,
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


def _build_global_rules_module(rendered_prompt: str) -> dict[str, Any]:
    """Extract the global-rules section from prompt_format (see design §6.4 §7)."""
    idx = rendered_prompt.find(_GLOBAL_RULES_MARKER)
    if idx == -1:
        # Be lenient: if the marker is missing, store the whole rendered text
        # as global rules — better than failing the whole init.
        logger.warning(
            "global rules marker '%s' not found in yaml.prompt_format; "
            "storing entire prompt as global_rules.ocr_prompt",
            _GLOBAL_RULES_MARKER,
        )
        rules_text = rendered_prompt
    else:
        rules_text = rendered_prompt[idx:]

    return {
        "module_key": "global_rules",
        "display_name": "全局规则与约束",
        "json_path": "$",
        "schema_fragment": {},  # contributes no schema (§6.4 §10 note)
        "ocr_suggestions": {
            "semantics": "全局规则不针对单个字段",
            "position": "适用于所有字段",
            "most_common_feature": "—",
            "extra_features": [],
        },
        "ocr_prompt": rules_text,
        "description": (
            "整张文档级别的提取规则集合：日期、数字格式、税种简称、跨页合并等。"
            "由 yaml.prompt_format 中『提取规则：』及之后所有段落组成。"
        ),
        "order_index": 0,
    }


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
