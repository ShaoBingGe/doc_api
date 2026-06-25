"""
Module initializer — auto-decompose an ApiDefinition.response_schema into
a starter set of OcrModules + creates the first OcrPromptVersion.

Decomposition strategy (docs/ocr-optimizer-design.md §6):
  1. Walk top-level schema.properties
  2. Each `type: array` field → its own module (json_path: $.field[*])
  3. Each `type: object` field → its own module (json_path: $.field)
  4. Scalar fields are grouped by naming convention:
       *_number / *_code / *_id     → "identifiers" module
       *_date / *_time / date / time → "temporal" module
       subtotal / tax / total / *_amount / currency → "totals" module
       all others → standalone modules
"""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models.api_definition import ApiDefinition

from ..models import (
    OcrModule,
    OcrPromptVersion,
    PromptVersionStatus,
    VersionOrigin,
)
from .composer import assemble_prompt, assemble_schema

logger = logging.getLogger(__name__)

# ── Grouping rules ────────────────────────────────────────────────────────────

_IDENTIFIER_SUFFIXES = ("_number", "_code", "_id", "_no", "_num")
_TEMPORAL_NAMES = {"date", "time"}
_TEMPORAL_SUFFIXES = ("_date", "_time", "_at")
_TOTALS_NAMES = {"subtotal", "tax", "total", "currency"}
_TOTALS_SUFFIXES = ("_amount", "_total", "_tax", "_subtotal")


def _classify_scalar(field_name: str) -> str | None:
    """Return a group key or None if the scalar should remain standalone."""
    name = field_name.lower()
    if name in _TEMPORAL_NAMES or name.endswith(_TEMPORAL_SUFFIXES):
        return "temporal"
    if name in _TOTALS_NAMES or name.endswith(_TOTALS_SUFFIXES):
        return "totals"
    if name.endswith(_IDENTIFIER_SUFFIXES):
        return "identifiers"
    return None


def _group_display_name(group_key: str) -> str:
    return {
        "temporal": "时间识别",
        "totals": "金额汇总识别",
        "identifiers": "编号识别",
    }.get(group_key, group_key)


# ── Public API ────────────────────────────────────────────────────────────────

def init_version(
    db: Session,
    api_definition_id: uuid.UUID,
    sample_document_ids: list[uuid.UUID] | None = None,
    activate: bool = True,
    use_llm_for_modules: bool = False,
) -> OcrPromptVersion:
    """
    Create the first OcrPromptVersion for an ApiDefinition.

    - Reads ApiDefinition.response_schema and decomposes into modules.
    - Optionally activates the new version (default True).
    - Optionally persists sample_document_ids into ApiDefinition.config.
    """
    api_def: ApiDefinition | None = db.get(ApiDefinition, api_definition_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_definition_id} not found")

    if not api_def.response_schema or not isinstance(api_def.response_schema, dict):
        raise ValidationError(
            "ApiDefinition.response_schema is required to initialize OCR modules"
        )

    # Persist sample_document_ids if provided (or normalize legacy single-id key)
    if sample_document_ids is not None:
        cfg = dict(api_def.config or {})
        cfg["sample_document_ids"] = [str(x) for x in sample_document_ids]
        # Drop legacy single-id field if present
        cfg.pop("sample_document_id", None)
        api_def.config = cfg

    # Determine next integer version number (version is now String; we use ints
    # for round/init products, "X.Y" for manual_edit). See run_orchestrator._next_round_version.
    labels = (
        db.query(OcrPromptVersion.version)
        .filter(OcrPromptVersion.api_definition_id == api_definition_id)
        .all()
    )
    used_ints: set[int] = set()
    for (label,) in labels:
        if label is None:
            continue
        s = str(label)
        if "." in s:
            continue
        try:
            used_ints.add(int(s))
        except ValueError:
            continue
    next_version_num = 1
    while next_version_num in used_ints:
        next_version_num += 1

    # Decompose schema
    module_specs = _decompose(api_def.response_schema, api_def.description or "")

    # Optionally use LLM to enrich description/suggestions/prompt
    if use_llm_for_modules:
        try:
            from .llm_call import enrich_module_with_llm
            for spec in module_specs:
                enrich_module_with_llm(spec, api_def)
        except Exception as exc:
            logger.warning("LLM enrichment failed, using fallback prompts: %s", exc)

    # Build a transient version (id pre-assigned so modules can FK)
    version = OcrPromptVersion(
        id=uuid.uuid4(),
        api_definition_id=api_definition_id,
        version=str(next_version_num),
        status=PromptVersionStatus.draft.value,
        origin=VersionOrigin.init.value,
        composed_prompt="",  # filled below
        composed_schema=None,
        notes="initial decomposition",
    )

    modules: list[OcrModule] = []
    for i, spec in enumerate(module_specs):
        modules.append(
            OcrModule(
                id=uuid.uuid4(),
                prompt_version_id=version.id,
                module_key=spec["module_key"],
                display_name=spec["display_name"],
                description=spec["description"],
                json_path=spec["json_path"],
                schema_fragment=spec["schema_fragment"],
                ocr_suggestions=spec["ocr_suggestions"],
                ocr_prompt=spec["ocr_prompt"],
                order_index=i,
            )
        )

    # Compose
    try:
        from . import field_constraints
        # Legacy path: this initializer doesn't know the country context.
        # Country-templated init lives in preset_init.py, which sets
        # OcrPromptVersion.country_global_text directly. Pass None so
        # composer skips the country section (composed_prompt still well-formed).
        # Customer per-field overrides still apply (returns "" when none).
        _cg = field_constraints.enforce(db, api_definition_id, modules, None) or None
        version.composed_schema = assemble_schema(modules)
        version.composed_prompt = assemble_prompt(modules, country_global=_cg)
    except ValueError as exc:
        raise ValidationError(f"Schema composition failed: {exc}")

    db.add(version)
    for m in modules:
        db.add(m)

    if activate:
        _deactivate_others(db, api_definition_id)
        version.status = PromptVersionStatus.active.value
        from datetime import datetime, timezone
        version.activated_at = datetime.now(timezone.utc)
        api_def.prompt_version_id = version.id

    db.commit()
    db.refresh(version)
    return version


# ── Decomposition ─────────────────────────────────────────────────────────────

def _decompose(schema: dict, context_desc: str) -> list[dict]:
    """
    Walk the top-level properties of a JSON Schema and emit module specs.

    Each spec is a dict with keys:
        module_key, display_name, description, json_path,
        schema_fragment, ocr_suggestions, ocr_prompt
    """
    props = schema.get("properties") or {}
    if not isinstance(props, dict) or not props:
        # Single-module fallback: the whole schema is one module
        return [
            {
                "module_key": "root",
                "display_name": "整体识别",
                "description": "提取文档全部字段",
                "json_path": "$",
                "schema_fragment": copy.deepcopy(schema),
                "ocr_suggestions": _default_suggestions(),
                "ocr_prompt": _default_prompt("整体识别", schema),
            }
        ]

    grouped: dict[str, list[tuple[str, dict]]] = {}
    specs: list[dict] = []

    for field_name, field_schema in props.items():
        if not isinstance(field_schema, dict):
            continue
        ftype = (field_schema.get("type") or "").lower()

        # Array field — own module
        if ftype == "array":
            json_path = f"$.{field_name}[*]"
            items_schema = field_schema.get("items") or {"type": "object"}
            specs.append(
                _make_spec(
                    module_key=field_name,
                    display_name=_human_name(field_name) + "识别",
                    json_path=json_path,
                    schema_fragment={
                        "type": "array",
                        "items": items_schema,
                    },
                    description=f"识别文档中的「{field_name}」表/数组，每行包含 "
                                f"{_list_keys(items_schema)}",
                )
            )
            continue

        # Object field — own module
        if ftype == "object":
            specs.append(
                _make_spec(
                    module_key=field_name,
                    display_name=_human_name(field_name) + "识别",
                    json_path=f"$.{field_name}",
                    schema_fragment=copy.deepcopy(field_schema),
                    description=f"识别文档中的「{field_name}」结构，包含 "
                                f"{_list_keys(field_schema)}",
                )
            )
            continue

        # Scalar — try to group
        group = _classify_scalar(field_name)
        if group:
            grouped.setdefault(group, []).append((field_name, field_schema))
        else:
            specs.append(
                _make_spec(
                    module_key=field_name,
                    display_name=_human_name(field_name) + "识别",
                    json_path=f"$.{field_name}",
                    schema_fragment=copy.deepcopy(field_schema),
                    description=f"识别文档中的单字段 {field_name}",
                )
            )

    # Emit grouped modules
    for group_key, items in grouped.items():
        sub_props = {name: copy.deepcopy(s) for name, s in items}
        specs.append(
            _make_spec(
                module_key=group_key,
                display_name=_group_display_name(group_key),
                json_path="$",  # group module merges its sub-fields into root
                schema_fragment={"type": "object", "properties": sub_props},
                description=f"识别一组相关字段：{', '.join(name for name, _ in items)}",
            )
        )
    return specs


def _make_spec(
    *,
    module_key: str,
    display_name: str,
    json_path: str,
    schema_fragment: dict,
    description: str,
) -> dict:
    return {
        "module_key": module_key,
        "display_name": display_name,
        "description": description,
        "json_path": json_path,
        "schema_fragment": schema_fragment,
        "ocr_suggestions": _default_suggestions(),
        "ocr_prompt": _default_prompt(display_name, schema_fragment, description),
    }


def _default_suggestions() -> dict:
    return {
        "semantics": "待优化器自动学习",
        "position": "待优化器自动学习",
        "most_common_feature": "待优化器自动学习",
        "extra_features": [],
    }


def _default_prompt(display_name: str, schema_fragment: dict, description: str = "") -> str:
    """Fallback prompt fragment when no LLM-generated prompt is available."""
    import json as _json

    keys_hint = _list_keys(schema_fragment) or "（按 schema 输出）"
    return (
        f"请从文档中识别 {display_name} 相关的字段。\n"
        f"{description}\n"
        f"应输出的字段：{keys_hint}\n"
        f"schema 子树：```json\n{_json.dumps(schema_fragment, ensure_ascii=False)}\n```\n"
        f"找不到的字段输出 null，不要捏造。"
    )


def _human_name(field_name: str) -> str:
    return field_name.replace("_", " ").title()


def _list_keys(schema_node: dict) -> str:
    if not isinstance(schema_node, dict):
        return ""
    if schema_node.get("type") == "object":
        props = schema_node.get("properties") or {}
        return ", ".join(props.keys())
    if schema_node.get("type") == "array":
        items = schema_node.get("items") or {}
        return _list_keys(items)
    return ""


def _deactivate_others(db: Session, api_definition_id: uuid.UUID) -> None:
    db.query(OcrPromptVersion).filter(
        OcrPromptVersion.api_definition_id == api_definition_id,
        OcrPromptVersion.status == PromptVersionStatus.active.value,
    ).update({"status": PromptVersionStatus.archived.value})
