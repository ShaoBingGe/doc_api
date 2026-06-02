"""
Composer — assembles the final `composed_prompt` string and
`composed_schema` dict from a list of OcrModule snapshots.

Layout (design v7 + prompt-v2 Phase 1 — see CLAUDE.md §① and
docs/prompt-system-v2-plan.md):
  - GLOBAL_PREAMBLE
  - GLOBAL_NAVIGATION (Phase 1 — reading map / progressive disclosure)
  - country_global_text (Part 1 + slim Part 2 from country yaml)
  - GLOBAL_SCHEMA_REFERENCE (composed schema block)
  - GLOBAL_OUTPUT_CONTRACT_DETAILS (Part 3, platform-wide, loaded from
    backend/app/ocr_optimizer/assets/global_output_contract.yaml)
  - "# 模块识别指令" + module-section intro + per-module bodies
    (each module prefixed with a consistent identity line: key / path / type)
  - GLOBAL_SELF_CHECK

Phase 1 adds navigation + per-module framing WITHOUT changing the render
order or the Part 3 content — §① stays intact.

The three GLOBAL_* constants + GLOBAL_OUTPUT_CONTRACT_DETAILS form the
**platform contract** — any path (client / optimizer / reflection) that tries
to rewrite them is a bug per CLAUDE.md §①.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Iterable

from .output_contract import render_output_contract


# ── Global template fragments (not module-iterable) ──────────────────────────

GLOBAL_PREAMBLE = """你是一名严谨的文档信息抽取专家。请阅读这张文档（图片或 PDF），\
并严格按下方指定的 JSON Schema 输出一份合法的 JSON。

# 通用约束
1. 仅输出 JSON，不要任何 markdown、解释或多余文字。
2. 字段缺失时输出 null，不要捏造。
3. 日期统一格式为 YYYY-MM-DD；数字去掉千分位与货币符号。
"""

# Prompt System v2 / Phase 1 — reading map (progressive disclosure).
# Gives the model the layout up front so it knows how the parts relate and in
# what order to use them: facts first, then per-field extraction, then assembly.
# IMPORTANT: this block must NOT contain the exact section-header strings the
# composer emits later (e.g. "# 整体输出 Schema", "# Part 3 · 输出契约与装配规则",
# "# 模块识别指令", "# 输出前自检") — downstream code/tests locate those headers
# by first occurrence, so the map refers to the parts by plain name only.
GLOBAL_NAVIGATION = """# 阅读导航
本提示词由三部分加逐字段指令组成，请按顺序理解：
1. Part 1（国家事实）：票据分类、语言/货币/日期格式、税号规则等「输入侧事实」与默认值。
2. Part 2（字段语义）：每个字段「在哪里找、找什么」——见下方整体 Schema 与各字段描述。
3. Part 3（输出契约）：找到值之后「如何组装成合法 JSON」的平台统一规则（数值规范、税额/行项目装配、缺失字段处理等）。
4. 逐字段取值指令：每个字段的业务语义、取值锚点、格式与排歧要点。

工作顺序：先用 Part 1 建立事实 → 按逐字段指令取值 → 用 Part 3 规则组装 → 按结尾自检校验后再输出。
"""

# Platform-wide output contract (Part 3). Loaded once at import time from
# the yaml asset; immutable thereafter for the process lifetime. Same
# protection level as GLOBAL_PREAMBLE / GLOBAL_SELF_CHECK.
GLOBAL_OUTPUT_CONTRACT_DETAILS = render_output_contract()

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

    Render order (design v7 + Phase 1):
        GLOBAL_PREAMBLE
        GLOBAL_NAVIGATION                 ← reading map (Phase 1)
        country_global                    ← Part 1 + slim Part 2 (skipped when empty)
        GLOBAL_SCHEMA_REFERENCE           ← composed schema JSON block
        GLOBAL_OUTPUT_CONTRACT_DETAILS    ← Part 3 platform contract
        # 模块识别指令 + intro
        ## 1..N field modules (each with an identity line)
        GLOBAL_SELF_CHECK
    """
    mod_list = list(modules)
    body_parts: list[str] = []
    for i, m in enumerate(mod_list, start=1):
        name = getattr(m, "display_name", None) or getattr(m, "module_key", f"module_{i}")
        # Phase 1 — consistent per-field identity line so the model parses every
        # module uniformly: which field, where its value lands, what type. The
        # ocr_prompt body (free text today; structured in Phase 2) follows.
        key = getattr(m, "module_key", "") or ""
        jp = getattr(m, "json_path", "") or ""
        frag = getattr(m, "schema_fragment", None)
        ftype = ""
        if isinstance(frag, dict):
            ftype = str(frag.get("type") or "").strip()
        ident_bits: list[str] = []
        if key:
            ident_bits.append(f"字段键 `{key}`")
        if jp:
            ident_bits.append(f"输出路径 `{jp}`")
        if ftype:
            ident_bits.append(f"类型 {ftype}")
        ident_line = ("- " + " · ".join(ident_bits) + "\n") if ident_bits else ""
        body = _render_module_body(m)
        body_parts.append(f"## {i}. {name}\n{ident_line}{body}\n")

    schema = assemble_schema(mod_list)
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)
    schema_reference = (
        "# 整体输出 Schema\n"
        "返回的 JSON 必须符合下列 Schema：\n"
        f"```json\n{schema_json}\n```\n"
    )
    country_section = ""
    if country_global and country_global.strip():
        country_section = f"\n{country_global.strip()}\n"

    modules_header = (
        "\n# 模块识别指令\n"
        "下列每个字段给出其业务语义与取值要点。请按字段逐一取值；"
        "字段值「找到后如何组装/格式化」的规则统一见上方 Part 3。"
        "最终 JSON 的 key 以每个字段标注的「字段键」为准。\n\n"
    )
    return (
        GLOBAL_PREAMBLE
        + "\n"
        + GLOBAL_NAVIGATION
        + "\n"
        + country_section
        + schema_reference
        + "\n"
        + GLOBAL_OUTPUT_CONTRACT_DETAILS
        + "\n"
        + modules_header
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


def _render_module_body(m) -> str:
    """Phase 2 — render a module's body.

    Opt-in structured path: if the module carries a renderable FieldRule
    (in-memory `field_rule` attr or persisted under ocr_suggestions), render
    its uniform skeleton (语义/取值锚点/格式/排歧/跨样本规则). Otherwise fall
    back to the raw ocr_prompt — so production modules authored before Phase 2
    render EXACTLY as in Phase 1 (no accuracy risk; the structured path is only
    taken once upstream producers populate a FieldRule in Phase 3+).

    Still pure string work — composer never calls an LLM (CLAUDE.md §③.4).
    """
    from .field_rule import field_rule_of

    fr = field_rule_of(m)
    if fr is not None and fr.is_renderable():
        return fr.render_skeleton()
    return (getattr(m, "ocr_prompt", "") or "").strip()


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
