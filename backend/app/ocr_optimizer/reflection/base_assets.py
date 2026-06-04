"""
Shared reflection base assets (Phase 5 — 公共基底).

The reflection skills and country agents share a doctrine block and an output
schema. Instead of repeating them in every YAML (which drifts), they live once
under `reflection/base/` and are injected into each prompt at render time via
`{base_doctrine}` / `{base_edit_output}` placeholders. See base/README.md.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent / "base"


@lru_cache(maxsize=8)
def base_text(name: str) -> str:
    p = _BASE_DIR / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def base_format_vars() -> dict[str, str]:
    """Placeholder → text map merged into every skill/agent render's format dict.
    Values contain literal JSON braces; they are injected (not re-formatted), so
    the braces survive verbatim."""
    return {
        "base_doctrine": base_text("doctrine.md"),
        "base_edit_output": base_text("edit_output.md"),
    }
