"""
Field constraints — first-class, sticky per-field user overrides.

Problem this solves (see CLAUDE.md §④ / customer-override doctrine):
  A customer who explicitly defines a field's TYPE (e.g. invoiceNumber →
  number) and/or a stripping behaviour (remove spaces / - / _ / * …) is
  expressing dissatisfaction with the platform default. The platform must
  NOT let its general knowledge — the country template's Part 1 facts, the
  field description, or the optimizer's per-round reflection — override that
  deliberate decision.

Before this module, a per-field type change had nowhere to persist and was
silently reverted: the module schema_fragment.type stayed at the country
default, every optimization round regenerated the ocr_prompt back toward the
country narrative ("字母数字混合，严格输出字符串"), and the final result drifted
back to the original type.

Design — a single deterministic enforcement layer, mirroring the post-OCR
projection fix (document_service._project_to_field_set):

  - PERSIST   overlay["field_constraints"] = {field: {type, strip_chars,
              strip_non_numeric, locked, note}} (pending_edits_service).
  - ENFORCE   at every version-composition choke point, AFTER the optimizer
              has (re)written modules, deterministically re-assert the
              constraint:
                · schema_fragment.type  ← override type
                · ocr_prompt            ← prepend a LOCKED block (idempotent)
                · country_global (Part1)← append an override section (idempotent)
              Because this runs after the reflection/optimizer, NOTHING the
              reflection produces can survive past it — precedence is
              user-override > country Part1 > learned reflection, guaranteed
              structurally rather than by prompt persuasion.
  - NORMALIZE the actual extracted value (document_service) so the stripping
              behaviour takes effect on the result regardless of what the VLM
              emits.

Pure-ish: load() reads the overlay; the apply_* / normalize_* helpers are
pure string/dict work and never call an LLM (CLAUDE.md §③.4).
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from typing import Any, Iterable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Markers delimiting the locked-constraint block we inject into a module's
# ocr_prompt. Used to STRIP a prior block before re-adding, so re-enforcing
# across rounds / manual patches never compounds.
_LOCK_START = "【用户硬约束·开始（最高优先级·不可被反思/Part1推翻）】"
_LOCK_END = "【用户硬约束·结束】"

# Same idea for the country-global (Part 1) override section.
_CG_OVERRIDE_HEADER = "# 用户硬约束覆盖（最高优先级）"

# JSON-schema type tokens are UPPERCASE in this codebase ("STRING"/"NUMBER"/…).
_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "int": "INTEGER",
    "float": "NUMBER",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
}

# Default special chars implied by "删除特殊字符" when the user enables stripping
# without naming an explicit set.
_DEFAULT_STRIP_CHARS = [" ", "-", "_", "*", "/", "\\", "(", ")", "　"]


# ── Constraint shape ──────────────────────────────────────────────────────────

def _norm_one(raw: dict | None) -> dict | None:
    """Normalize a single constraint dict; return None if it's a no-op."""
    if not isinstance(raw, dict):
        return None
    t = str(raw.get("type") or "").strip().lower()
    schema_type = _TYPE_MAP.get(t) if t else None
    strip_chars = list(raw.get("strip_chars") or [])
    strip_non_numeric = bool(raw.get("strip_non_numeric"))
    # number/integer with no explicit strip set ⇒ strip all non-numeric by default
    if schema_type in {"NUMBER", "INTEGER"} and not strip_chars and "strip_non_numeric" not in raw:
        strip_non_numeric = True
    locked = raw.get("locked", True)
    note = str(raw.get("note") or "").strip()
    # A constraint with neither a type nor any stripping behaviour does nothing.
    if not schema_type and not strip_chars and not strip_non_numeric:
        return None
    return {
        "type": schema_type,                 # UPPERCASE schema token or None
        "strip_chars": strip_chars,
        "strip_non_numeric": strip_non_numeric,
        "locked": bool(locked),
        "note": note,
    }


def load(db: Session, api_def_id: uuid.UUID) -> dict[str, dict]:
    """Return {field_name: normalized_constraint} for this ApiDef (empty if none)."""
    try:
        from app.services import pending_edits_service
        overlay = pending_edits_service.get_overlay(db, api_def_id)
    except Exception:  # noqa: BLE001
        return {}
    raw = overlay.get("field_constraints") or {}
    out: dict[str, dict] = {}
    for field, spec in raw.items():
        norm = _norm_one(spec)
        if norm:
            out[str(field)] = norm
    return out


# ── Value-level normalization (the "stripping must take effect" guarantee) ────

def _leaf(json_path: str) -> str:
    """invoiceNumber leaf name from a json_path like $[*].invoiceNumber."""
    if not json_path:
        return ""
    leaf = json_path.split(".")[-1]
    return leaf.replace("[*]", "").replace("[", "").replace("]", "").strip()


def normalize_value(value: Any, c: dict) -> Any:
    """Deterministically apply a constraint to a single scalar value.

    Strips the configured chars, optionally removes all non-numeric chars,
    then coerces to the declared type. Returns None when stripping empties
    the value (never emits a meaningless residue).
    """
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value  # constraints target scalar leaves only
    s = str(value)
    for ch in (c.get("strip_chars") or []):
        if ch:
            s = s.replace(ch, "")
    t = c.get("type")
    if c.get("strip_non_numeric") or t in {"NUMBER", "INTEGER"}:
        # keep digits, one leading sign, decimal point
        neg = s.strip().startswith("-")
        digits = re.sub(r"[^0-9.]", "", s)
        # collapse multiple dots to the first
        if digits.count(".") > 1:
            head, _, tail = digits.partition(".")
            digits = head + "." + tail.replace(".", "")
        s = ("-" + digits) if (neg and digits) else digits
    s = s.strip()
    if s == "":
        return None
    if t == "NUMBER":
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except ValueError:
            return None
    if t == "INTEGER":
        try:
            return int(float(s))
        except ValueError:
            return None
    return s


def normalize_record_fields(record: dict, constraints: dict[str, dict]) -> dict:
    """Apply constraints to a flat extraction record's top-level fields.

    Used by document_service on the normalized leaf-list AND on record-dict
    shapes. Mutates a copy, keyed by exact field name.
    """
    if not constraints or not isinstance(record, dict):
        return record
    out = dict(record)
    for k, v in record.items():
        c = constraints.get(k)
        if c is not None:
            out[k] = normalize_value(v, c)
    return out


# ── Module / prompt / Part1 enforcement (the "survive reflection" guarantee) ──

def _lock_block(field: str, c: dict) -> str:
    lines = [_LOCK_START, f"- 字段 `{field}` 由客户显式锁定，优先级高于 Part 1 国家事实、字段描述与历次反思结论。"]
    if c.get("type"):
        lines.append(f"- 输出类型必须为 **{c['type']}**；不得输出其它类型。")
    strip = list(c.get("strip_chars") or [])
    if c.get("strip_non_numeric"):
        lines.append("- 输出前必须删除所有非数字字符（字母、空格、- _ * / 括号、货币符号等），最终只保留数字（可含一个小数点与负号）。")
    elif strip:
        shown = " ".join(repr(x) for x in strip)
        lines.append(f"- 输出前必须删除以下字符：{shown}。")
    if c.get("note"):
        lines.append(f"- 备注：{c['note']}")
    lines.append("- 下文与 Part 1 中任何「字母数字混合 / 保留原样 / 输出字符串」等与本约束冲突的说明、示例，一律忽略。")
    lines.append(_LOCK_END)
    return "\n".join(lines)


def _strip_existing_lock(prompt: str) -> str:
    """Remove any previously-injected lock block (idempotent re-enforcement)."""
    if _LOCK_START not in prompt:
        return prompt
    # Remove every START..END region (greedy-safe via non-greedy DOTALL).
    cleaned = re.sub(
        re.escape(_LOCK_START) + r".*?" + re.escape(_LOCK_END),
        "",
        prompt,
        flags=re.DOTALL,
    )
    return cleaned.lstrip("\n")


def apply_to_modules(modules: Iterable, constraints: dict[str, dict]) -> None:
    """Mutate modules IN PLACE so each locked field's schema_fragment.type and
    ocr_prompt reflect the constraint.

    deepcopy schema_fragment before mutating — clone_modules_to_new_version
    copies the fragment BY REFERENCE from the base module, so an in-place edit
    would otherwise leak into prior versions.
    """
    if not constraints:
        return
    for m in modules:
        field = _leaf(getattr(m, "json_path", "") or "")
        c = constraints.get(field)
        if not c:
            continue
        # 1) schema type
        if c.get("type"):
            frag = getattr(m, "schema_fragment", None)
            frag = copy.deepcopy(frag) if isinstance(frag, dict) else {}
            frag["type"] = c["type"]
            # a numeric override invalidates a string-era enum
            if c["type"] in {"NUMBER", "INTEGER"}:
                frag.pop("enum", None)
            m.schema_fragment = frag
        # 2) locked block at the TOP of the body (idempotent)
        base = _strip_existing_lock((getattr(m, "ocr_prompt", "") or "").strip())
        m.ocr_prompt = _lock_block(field, c) + "\n\n" + base


def augment_country_global(country_global: str | None, constraints: dict[str, dict]) -> str:
    """Append (idempotently) a 'user override' section to the country-global
    Part 1 text, so the final prompt states the overrides AFTER the country
    facts (last word wins) and explicitly voids conflicting Part 1 examples.

    Pass the CLEAN country_global_text — this returns an augmented copy for
    composing; callers must NOT persist the augmented text back to
    country_global_text (avoids cross-round compounding).
    """
    base = (country_global or "")
    # strip any prior override section we may have appended before
    idx = base.find(_CG_OVERRIDE_HEADER)
    if idx >= 0:
        base = base[:idx].rstrip()
    if not constraints:
        return base
    lines = ["", _CG_OVERRIDE_HEADER,
             "以下字段以客户显式定义为准；Part 1 与各字段描述中与之冲突的格式说明/示例一律作废："]
    for field, c in sorted(constraints.items()):
        bits = []
        if c.get("type"):
            bits.append(f"类型必须为 {c['type']}")
        if c.get("strip_non_numeric"):
            bits.append("删除所有非数字字符后输出纯数字")
        elif c.get("strip_chars"):
            bits.append("删除字符 " + " ".join(repr(x) for x in c["strip_chars"]))
        if not bits:
            continue
        lines.append(f"- `{field}`：" + "；".join(bits) + "。")
    return (base.rstrip() + "\n" + "\n".join(lines) + "\n") if len(lines) > 3 else base


def enforce(
    db: Session,
    api_def_id: uuid.UUID,
    modules: Iterable,
    country_global: str | None,
) -> str:
    """Convenience for composition sites: load constraints, mutate modules
    in place, and return the augmented country_global to pass to the composer.

    No-op (returns country_global unchanged) when there are no constraints.
    """
    constraints = load(db, api_def_id)
    if not constraints:
        return country_global or ""
    mod_list = list(modules)
    apply_to_modules(mod_list, constraints)
    logger.info(
        "field_constraints: enforced %d locked field(s) on ApiDef %s: %s",
        len(constraints), api_def_id, sorted(constraints.keys()),
    )
    return augment_country_global(country_global, constraints)
