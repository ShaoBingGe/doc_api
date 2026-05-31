"""
Country-specific reflection agents.

Per CLAUDE.md §⑤ each country may define its own pair of reflection agents:
  - add_field  (kind=add) — for customer-added fields
  - edit_field (kind=edit) — for customer-corrected fields

Files live under `reflection/country_agents/<COUNTRY>/{add_field,edit_field}.yaml`.

These are PRODUCT-TEAM maintained assets (not customer-facing). They can be
edited via the workspace's "迭代过程" tab; saving writes the YAML back.

Schema:
  key: str (unique)
  display_name: str
  country: str (uppercase ISO-ish, MY/CN/US/…)
  kind: 'add' | 'edit'
  version: int
  remark: str (multi-line; internal notes)
  system_prompt: str
  user_prompt_template: str (uses .format() placeholders)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parent / "country_agents"
VALID_KINDS = ("add", "edit")


@dataclass
class CountryAgent:
    """A single country-specific reflection agent (one kind)."""
    key: str
    display_name: str
    country: str
    kind: str  # 'add' | 'edit'
    version: int
    remark: str
    system_prompt: str
    user_prompt_template: str
    file_path: Path

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "country": self.country,
            "kind": self.kind,
            "version": self.version,
            "remark": self.remark,
            "system_prompt": self.system_prompt,
            "user_prompt_template": self.user_prompt_template,
        }

    def render(self, diff: dict, ctx: dict | None = None) -> str:
        """Fill in the user_prompt_template with diff + context fields."""
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
            "schema_type": (ctx or {}).get("schema_type") or diff.get("corrected_format") or "",
        }
        try:
            return self.user_prompt_template.format(**_FormatDict(merged))
        except Exception as exc:
            logger.exception("CountryAgent %s render failed: %s", self.key, exc)
            return self.user_prompt_template


class _FormatDict(dict):
    """str.format helper — missing keys yield '' instead of KeyError."""
    def __missing__(self, key):
        return ""


def _safe_str(v: Any) -> str:
    if v is None:
        return "(空)"
    if isinstance(v, str):
        return v if v.strip() else "(空)"
    return str(v)


# ── Loader ───────────────────────────────────────────────────────────────


def _country_dir(country: str) -> Path:
    return _AGENTS_DIR / country.upper()


def list_countries() -> list[str]:
    """List countries that have at least one agent YAML."""
    if not _AGENTS_DIR.exists():
        return []
    return sorted(
        d.name for d in _AGENTS_DIR.iterdir()
        if d.is_dir() and any(d.glob("*.yaml"))
    )


def load_country_agents(country: str) -> dict[str, CountryAgent]:
    """Return {kind: CountryAgent} for the given country, empty dict if none."""
    if not country:
        return {}
    country = country.upper()
    out: dict[str, CountryAgent] = {}
    dir_path = _country_dir(country)
    if not dir_path.exists():
        return out
    for kind in VALID_KINDS:
        fpath = dir_path / f"{kind}_field.yaml"
        if not fpath.exists():
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            out[kind] = CountryAgent(
                key=data.get("key") or f"{country.lower()}_{kind}_field_reflection",
                display_name=data.get("display_name") or f"{country} {kind} field reflection",
                country=country,
                kind=kind,
                version=int(data.get("version") or 1),
                remark=str(data.get("remark") or ""),
                system_prompt=str(data.get("system_prompt") or ""),
                user_prompt_template=str(data.get("user_prompt_template") or ""),
                file_path=fpath,
            )
        except Exception as exc:
            logger.exception("Failed to load country agent %s: %s", fpath, exc)
    return out


def save_country_agent(
    *,
    country: str,
    kind: str,
    display_name: str | None = None,
    remark: str | None = None,
    system_prompt: str,
    user_prompt_template: str,
) -> CountryAgent:
    """Persist an agent's config back to its YAML file. Bumps version."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    country = country.upper()
    dir_path = _country_dir(country)
    dir_path.mkdir(parents=True, exist_ok=True)
    fpath = dir_path / f"{kind}_field.yaml"

    existing = load_country_agents(country).get(kind)
    new_version = (existing.version + 1) if existing else 1
    payload = {
        "key": existing.key if existing else f"{country.lower()}_{kind}_field_reflection",
        "display_name": display_name or (existing.display_name if existing else f"{country} {kind} 字段反思 Agent"),
        "country": country,
        "kind": kind,
        "version": new_version,
        "remark": remark or (existing.remark if existing else ""),
        "system_prompt": system_prompt,
        "user_prompt_template": user_prompt_template,
    }
    with open(fpath, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False, width=1000)
    logger.info("Saved country agent %s/%s → v%d", country, kind, new_version)

    return CountryAgent(
        file_path=fpath,
        **{k: v for k, v in payload.items() if k != "file_path"},
    )
