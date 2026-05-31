"""
Composer — assembles the final `composed_prompt` string and
`composed_schema` dict from a list of OcrModule snapshots.

Layout (see docs/ocr-optimizer-design.md §10):
  - GLOBAL_PREAMBLE
  - GLOBAL_OUTPUT_CONTRACT (mentions the composed schema)
  - For each module: "## N. {display_name}\n{ocr_prompt}\n"
  - GLOBAL_SELF_CHECK
"""

from __future__ import annotations

import copy
import json
import re
from typing import Iterable


# ── Global template fragments (not module-iterable) ──────────────────────────

GLOBAL_PREAMBLE = """你是一名严谨的文档信息抽取专家。请阅读这张文档（图片或 PDF），\
并严格按下方指定的 JSON Schema 输出一份合法的 JSON。

# 通用约束
1. 仅输出 JSON，不要任何 markdown、解释或多余文字。
2. 字段缺失时输出 null，不要捏造。
3. 日期统一格式为 YYYY-MM-DD；数字去掉千分位与货币符号。
"""

GLOBAL_SELF_CHECK = """
# 输出前自检
1. JSON 合法、可被 json.loads 解析。
2. 每个识别模块的字段都在最终 JSON 中存在（没有的填 null）。
3. 没有任何字段是 markdown 或自然语言描述。
"""


# ── Public API ────────────────────────────────────────────────────────────────

def assemble_prompt(modules: Iterable, *, country_global: str | None) -> str:
    """
    Concatenate global frame + country-wide rules + each module's ocr_prompt
    into a single composed prompt string.

    `modules` should already be sorted by order_index (the caller's
    responsibility, or rely on the relationship's order_by).

    `country_global` is REQUIRED (keyword-only) to force every caller to
    decide explicitly. Pass:
      - the version's `country_global_text` for country-templated ApiDefs
      - `None` or "" for non-templated ApiDefs (no country section rendered)

    Render order (design v6):
        GLOBAL_PREAMBLE
        country_global       ← injected here (skipped when empty)
        GLOBAL_OUTPUT_CONTRACT (composed schema)
        # 模块识别指令
        ## 1..N field modules
        GLOBAL_SELF_CHECK
    """
    mod_list = list(modules)
    body_parts: list[str] = []
    for i, m in enumerate(mod_list, start=1):
        name = getattr(m, "display_name", None) or getattr(m, "module_key", f"module_{i}")
        body_parts.append(f"## {i}. {name}\n{m.ocr_prompt.strip()}\n")

    schema = assemble_schema(mod_list)
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)
    output_contract = (
        "# 整体输出 Schema\n"
        "返回的 JSON 必须符合下列 Schema：\n"
        f"```json\n{schema_json}\n```\n"
    )
    country_section = ""
    if country_global and country_global.strip():
        country_section = f"\n{country_global.strip()}\n"

    return (
        GLOBAL_PREAMBLE
        + "\n"
        + country_section
        + output_contract
        + "\n# 模块识别指令\n"
        + "\n".join(body_parts)
        + GLOBAL_SELF_CHECK
    )


def assemble_schema(modules: Iterable) -> dict:
    """
    Merge each module's `schema_fragment` into a single top-level JSON Schema.

    For each module we use the `json_path` to know where in the final schema
    its fragment lives. Conflicting paths (two modules claim the same path)
    raise ValueError so callers can surface the misconfiguration.
    """
    root: dict = {"type": "object", "properties": {}}
    seen_paths: dict[str, str] = {}

    for m in modules:
        path = (m.json_path or "").strip()
        fragment = copy.deepcopy(m.schema_fragment or {})
        if not path:
            continue
        norm = _normalize_path(path)

        # Root-targeted modules (json_path = "$") merge their fragment's
        # properties into root.properties (grouped scalar modules use this).
        # Multiple root modules are allowed as long as their property keys
        # do not collide.
        if norm == "":
            if not isinstance(fragment, dict):
                continue
            frag_props = fragment.get("properties") or {}
            for prop_key, prop_schema in frag_props.items():
                if prop_key in root["properties"]:
                    raise ValueError(
                        f"Schema conflict: property '{prop_key}' is claimed "
                        f"by module '{m.module_key}' and another root module"
                    )
                root["properties"][prop_key] = prop_schema
            continue

        if norm in seen_paths:
            raise ValueError(
                f"Schema conflict: modules '{seen_paths[norm]}' and "
                f"'{m.module_key}' both claim json_path '{path}'"
            )
        seen_paths[norm] = m.module_key

        _inject(root, norm, fragment)
    return root


# ── Internals ─────────────────────────────────────────────────────────────────

_BRACKETS_RE = re.compile(r"\[(\*|\d+)\]")


def _normalize_path(path: str) -> str:
    p = path.strip()
    if p.startswith("$"):
        p = p[1:]
    if p.startswith("."):
        p = p[1:]
    # collapse [N] -> [*]
    p = _BRACKETS_RE.sub("[*]", p)
    return p


def _inject(schema: dict, norm_path: str, fragment: dict) -> None:
    """
    Inject `fragment` into `schema` at `norm_path`.

    Path tokens:
      - "foo"     → schema.properties.foo
      - "foo[*]"  → schema.properties.foo.{type:array, items: ...}
    """
    if not norm_path:
        # fragment replaces root — only valid if schema is empty
        if schema.get("properties"):
            raise ValueError("Cannot inject root fragment when other modules exist")
        schema.clear()
        schema.update(fragment)
        return

    tokens = _tokenize(norm_path)
    cursor = schema
    for i, (kind, key) in enumerate(tokens):
        is_last = i == len(tokens) - 1

        if kind == "key":
            cursor.setdefault("type", "object")
            cursor.setdefault("properties", {})
            if is_last:
                # Merge instead of overwrite to allow grouped modules to coexist
                existing = cursor["properties"].get(key)
                if existing and isinstance(existing, dict):
                    cursor["properties"][key] = _merge_schema(existing, fragment)
                else:
                    cursor["properties"][key] = fragment
                return
            # descend
            next_token_is_array = tokens[i + 1][0] == "array"
            child = cursor["properties"].setdefault(key, {})
            if next_token_is_array:
                child.setdefault("type", "array")
                child.setdefault("items", {})
                cursor = child["items"]
            else:
                cursor = child

        elif kind == "array":
            # already advanced into items in the previous step; the array
            # token itself is a no-op here unless it appears at the very end
            if is_last:
                # path ends with [*] meaning the module IS the array items schema
                # need to back up: caller fragment becomes the items schema.
                # We achieved that already by descending into items above when
                # the previous step set it up, so nothing more to do.
                return


def _tokenize(norm_path: str) -> list[tuple[str, str]]:
    """
    Tokenize a normalized path like "items[*].name" into:
      [("key","items"), ("array","*"), ("key","name")]
    """
    out: list[tuple[str, str]] = []
    for segment in norm_path.split("."):
        if not segment:
            continue
        m_iter = list(_BRACKETS_RE.finditer(segment))
        if not m_iter:
            out.append(("key", segment))
            continue
        key_part = segment[: m_iter[0].start()]
        if key_part:
            out.append(("key", key_part))
        for m in m_iter:
            out.append(("array", m.group(1)))
    return out


def _merge_schema(existing: dict, incoming: dict) -> dict:
    """
    Shallow-merge two schema dicts: incoming wins on conflicts EXCEPT for
    `properties`, which are deep-merged so two modules can contribute keys
    to the same object (e.g. "buyer.name" and "buyer.tax_id").
    """
    out = dict(existing)
    for k, v in incoming.items():
        if k == "properties" and isinstance(v, dict) and isinstance(existing.get("properties"), dict):
            merged_props = dict(existing["properties"])
            for prop_k, prop_v in v.items():
                if isinstance(prop_v, dict) and isinstance(merged_props.get(prop_k), dict):
                    merged_props[prop_k] = _merge_schema(merged_props[prop_k], prop_v)
                else:
                    merged_props[prop_k] = prop_v
            out["properties"] = merged_props
        else:
            out[k] = v
    return out
