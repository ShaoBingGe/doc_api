"""
Platform-wide output contract loader.

Loads `app/ocr_optimizer/assets/global_output_contract.yaml` once at import
time and renders it into a prompt-ready text block. Injected by composer.py
between the schema reference block and per-module bodies — see CLAUDE.md §①
for the protection contract.

The rendered text is the operational source of truth. The same content also
exists as a human-readable summary inside each country yaml's Part 3 (purely
documentation; composer does not read country-yaml Part 3 at runtime).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_ASSET_PATH = Path(__file__).resolve().parent.parent / "assets" / "global_output_contract.yaml"


@lru_cache(maxsize=1)
def render_output_contract() -> str:
    """Render the platform output contract as a prompt-ready text block.

    Cached for process lifetime — yaml is read once at first call.
    Any edits to the yaml require a process restart to take effect (this is
    a deliberate property: composed_prompt snapshots stay reproducible).
    """
    if not _ASSET_PATH.exists():
        raise FileNotFoundError(
            f"global_output_contract.yaml not found at {_ASSET_PATH}. "
            "This file is a required platform asset."
        )

    data = yaml.safe_load(_ASSET_PATH.read_text(encoding="utf-8"))

    if not isinstance(data, dict) or "sections" not in data:
        raise ValueError(
            f"global_output_contract.yaml is malformed: missing 'sections' key"
        )

    lines: list[str] = ["# Part 3 · 输出契约与装配规则（平台统一）"]
    for sec in data["sections"]:
        key = sec.get("key", "")
        title = sec.get("title", "")
        text = (sec.get("text") or "").rstrip()
        lines.append(f"\n## {key} {title}")
        lines.append(text)
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_tax_category_standard_abbreviations() -> list[str]:
    """Expose the platform's standard tax-category abbreviation list."""
    if not _ASSET_PATH.exists():
        return []
    data = yaml.safe_load(_ASSET_PATH.read_text(encoding="utf-8"))
    return list(data.get("tax_category_standard_abbreviations") or [])
