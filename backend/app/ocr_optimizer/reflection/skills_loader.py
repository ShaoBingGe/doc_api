"""
YAML skill registry.

Skills are reusable, product-team-maintained reflection capabilities. Each
YAML file under reflection/skills/ defines:
  - key: unique identifier
  - display_name: human label
  - version: bump when prompt changes
  - match: a structured matcher (see Skill.matches)
  - prompt: the LLM prompt template, with {placeholders}

This module loads them eagerly at import time and exposes a Skill class with
a `matches(diff)` predicate and a `render(diff, context)` method.

Skills are intentionally NOT customer-facing — the YAML files live in the
codebase, modified via PR by product/tech.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass
class Skill:
    key: str
    display_name: str
    version: int
    match_spec: dict
    prompt_template: str

    # ── match() ──────────────────────────────────────────────────────────
    def matches(self, diff: dict) -> bool:
        """Return True if this skill applies to the given diff.

        diff schema:
          {
            "kind": "edit" | "add",
            "module_key": str | None (for add — may be empty),
            "original_name": str | None,
            "corrected_name": str,
            "original_value": Any,
            "corrected_value": Any,
            "original_format": str | None,    # e.g. "string"
            "corrected_format": str | None,
          }
        """
        spec = self.match_spec or {}
        kind_required = spec.get("diff_kind")
        if kind_required and diff.get("kind") != kind_required:
            return False

        any_of = spec.get("any_of")
        all_of_rest = {k: v for k, v in spec.items() if k not in {"diff_kind", "any_of"}}

        def check_one(predicate_key: str, expected: Any) -> bool:
            return self._eval_predicate(predicate_key, expected, diff)

        # AND over all top-level predicates (other than any_of)
        for k, v in all_of_rest.items():
            if not check_one(k, v):
                return False
        # any_of: at least one predicate inside must hold
        if any_of:
            ok = False
            for pred_dict in any_of:
                if isinstance(pred_dict, dict):
                    if all(check_one(k, v) for k, v in pred_dict.items()):
                        ok = True
                        break
            if not ok:
                return False
        return True

    @staticmethod
    def _eval_predicate(key: str, expected: Any, diff: dict) -> bool:
        if key == "original_value_is_empty":
            v = diff.get("original_value")
            actual = v is None or (isinstance(v, str) and v.strip() == "")
            return actual == expected
        if key == "corrected_value_is_empty":
            v = diff.get("corrected_value")
            actual = v is None or (isinstance(v, str) and v.strip() == "")
            return actual == expected
        if key == "value_changed":
            actual = diff.get("original_value") != diff.get("corrected_value")
            return actual == expected
        if key == "name_changed":
            actual = (diff.get("original_name") or "") != (diff.get("corrected_name") or "")
            return actual == expected
        if key == "format_changed":
            actual = (diff.get("original_format") or "") != (diff.get("corrected_format") or "")
            return actual == expected
        # Unknown predicate — log and skip (don't crash on author typos)
        logger.warning("Unknown skill match predicate: %s", key)
        return False

    # ── render() ─────────────────────────────────────────────────────────
    def render(self, diff: dict, ctx: dict[str, Any] | None = None) -> str:
        """Fill the prompt template with diff fields + context fields.

        Missing placeholders are replaced with empty string (we don't fail
        when a skill doesn't reference some field).
        """
        merged: dict[str, Any] = {
            "module_key": diff.get("module_key") or "",
            "display_name": diff.get("corrected_name") or diff.get("original_name") or "",
            "description": (ctx or {}).get("description") or "",
            "original_ocr_prompt": (ctx or {}).get("ocr_prompt") or "",
            "sibling_examples": (ctx or {}).get("sibling_examples") or "",
            "original_value": _safe_str(diff.get("original_value")),
            "corrected_value": _safe_str(diff.get("corrected_value")),
            "original_name": diff.get("original_name") or "",
            "corrected_name": diff.get("corrected_name") or "",
            "original_format": diff.get("original_format") or "",
            "corrected_format": diff.get("corrected_format") or "",
        }
        try:
            return self.prompt_template.format(**_FormatDict(merged))
        except Exception as exc:
            logger.exception("Skill %s render failed: %s", self.key, exc)
            return self.prompt_template


class _FormatDict(dict):
    """str.format helper that yields '' for missing keys instead of KeyError."""

    def __missing__(self, key):
        return ""


def _safe_str(v: Any) -> str:
    if v is None:
        return "(空)"
    if isinstance(v, str):
        return v if v.strip() else "(空)"
    return str(v)


# ── Loader ───────────────────────────────────────────────────────────────


_CACHE: list[Skill] | None = None


def load_skills(force: bool = False) -> list[Skill]:
    """Load all skill YAML files from reflection/skills/.

    Cached on first call (cheap dev-only iteration: pass force=True to reload).
    """
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE

    skills: list[Skill] = []
    if not _SKILLS_DIR.exists():
        logger.warning("Reflection skills dir not found: %s", _SKILLS_DIR)
        _CACHE = skills
        return skills

    for path in sorted(_SKILLS_DIR.glob("*.yaml")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            skill = Skill(
                key=data.get("key") or path.stem,
                display_name=data.get("display_name") or path.stem,
                version=int(data.get("version") or 1),
                match_spec=data.get("match") or {},
                prompt_template=data.get("prompt") or "",
            )
            skills.append(skill)
        except Exception as exc:
            logger.exception("Failed to load skill %s: %s", path.name, exc)

    _CACHE = skills
    logger.info("Loaded %d reflection skills: %s", len(skills), [s.key for s in skills])
    return skills
