"""
Schema Generator — 从结构化数据推断 JSON Schema。

公开接口：
    infer(structured_data: dict) -> dict   # 返回合法 JSON Schema (Draft 7)

特性：
  - 原子类型检测：string / number / integer / boolean / null
  - ISO 8601 日期/时间识别（string + format: date | date-time）
  - 嵌套 object 递归处理
  - array 支持（同构 → items schema；异构 → anyOf）
  - 字段描述自动生成（基于 key name + 值类型）
"""

from __future__ import annotations

import re
from typing import Any

# ── 日期/时间正则 ─────────────────────────────────────────────────────────────
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)


# ── Key 描述词典（关键词 → 可读描述片段）────────────────────────────────────────
_KEY_HINTS: dict[str, str] = {
    "id": "Unique identifier",
    "uuid": "UUID identifier",
    "name": "Display name",
    "title": "Title",
    "description": "Human-readable description",
    "status": "Status value",
    "type": "Type discriminator",
    "date": "Date value",
    "time": "Time value",
    "created": "Creation timestamp",
    "updated": "Last updated timestamp",
    "amount": "Monetary amount",
    "price": "Price",
    "total": "Total value",
    "count": "Count",
    "number": "Numeric identifier or count",
    "url": "URL",
    "email": "Email address",
    "phone": "Phone number",
    "address": "Address",
    "city": "City",
    "country": "Country",
    "code": "Code or short identifier",
    "key": "Key",
    "value": "Value",
    "data": "Raw data payload",
    "items": "List of items",
    "list": "List",
    "tags": "Tags or labels",
    "metadata": "Metadata",
    "config": "Configuration",
    "enabled": "Feature flag",
    "active": "Active flag",
    "flag": "Boolean flag",
}


def _auto_description(key: str, schema: dict) -> str:
    """Generate a short description from the field key and its inferred schema."""
    key_lower = key.lower()

    # 精确匹配或子串匹配关键词
    for hint_key, hint_desc in _KEY_HINTS.items():
        if hint_key == key_lower or key_lower.endswith(f"_{hint_key}") or key_lower.startswith(f"{hint_key}_"):
            break
        if hint_key in key_lower:
            hint_desc = hint_desc
            break
    else:
        # 无匹配时按类型生成通用描述
        schema_type = schema.get("type", "value")
        fmt = schema.get("format", "")
        if fmt:
            hint_desc = f"{fmt.capitalize()} field"
        elif schema_type == "object":
            hint_desc = "Nested object"
        elif schema_type == "array":
            hint_desc = "Array of values"
        else:
            hint_desc = f"{schema_type.capitalize()} field"

    # 把 snake_case / camelCase key 转成可读标签
    readable = key.replace("_", " ").replace("-", " ")
    return f"{hint_desc} ({readable})"


def _infer_value(value: Any) -> dict:
    """Return a JSON Schema fragment for a single value."""
    if value is None:
        return {"type": "null"}

    if isinstance(value, bool):
        return {"type": "boolean"}

    if isinstance(value, int):
        return {"type": "integer"}

    if isinstance(value, float):
        return {"type": "number"}

    if isinstance(value, str):
        if _RE_DATETIME.match(value):
            return {"type": "string", "format": "date-time"}
        if _RE_DATE.match(value):
            return {"type": "string", "format": "date"}
        return {"type": "string"}

    if isinstance(value, list):
        return _infer_array(value)

    if isinstance(value, dict):
        return _infer_object(value)

    # fallback
    return {"type": "string"}


def _infer_array(arr: list) -> dict:
    """Infer an array schema. Tries to produce a unified items schema."""
    if not arr:
        return {"type": "array", "items": {}}

    item_schemas = [_infer_value(v) for v in arr]

    # Deduplicate by JSON representation
    seen: list[dict] = []
    for s in item_schemas:
        if s not in seen:
            seen.append(s)

    if len(seen) == 1:
        return {"type": "array", "items": seen[0]}

    return {"type": "array", "items": {"anyOf": seen}}


def _infer_object(obj: dict) -> dict:
    """Recursively infer an object schema with descriptions."""
    if not obj:
        return {"type": "object", "properties": {}}

    properties: dict[str, dict] = {}
    for key, value in obj.items():
        prop_schema = _infer_value(value)
        prop_schema["description"] = _auto_description(key, prop_schema)
        properties[key] = prop_schema

    return {
        "type": "object",
        "properties": properties,
        "required": list(obj.keys()),
    }


def infer(structured_data: dict) -> dict:
    """
    Infer a JSON Schema (Draft 7) from *structured_data*.

    Parameters
    ----------
    structured_data:
        A plain Python dict representing one record (e.g. a parsed invoice).

    Returns
    -------
    dict
        A valid JSON Schema document with ``$schema``, ``type``, ``properties``,
        ``required``, and per-field ``description`` entries.
    """
    if not isinstance(structured_data, dict):
        raise TypeError(f"Expected dict, got {type(structured_data).__name__}")

    schema = _infer_object(structured_data)
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    # Move $schema to front for readability
    ordered = {"$schema": schema.pop("$schema")}
    ordered.update(schema)
    return ordered
