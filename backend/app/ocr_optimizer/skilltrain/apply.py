"""Apply typed FieldEdits to a skill document (ADR-001 P1).

The skill document is the per-field rule text. Edits are bounded:
  - append      → add a rule line under the field's section (the common case)
  - replace     → swap the field's existing rule block for new content
  - delete      → remove the field's rule block

Pure string work; deterministic; the "diff size" is bounded by the number of
edits (no whole-document rewrite). Section markers keep edits idempotent.
"""
from __future__ import annotations

import re
from typing import Iterable

from .types import FieldEdit

_SECTION = "## [field:{field}]"


def _section_re(field: str) -> re.Pattern:
    # Capture a "## [field:X] ... " block up to the next "## [field:" or EOF.
    head = re.escape(_SECTION.format(field=field))
    return re.compile(head + r".*?(?=\n## \[field:|\Z)", re.DOTALL)


def apply_edit(doc: str, edit: FieldEdit) -> str:
    """Apply one edit to the skill document and return the new document."""
    field = edit.target
    sec_re = _section_re(field)
    m = sec_re.search(doc)

    if edit.op == "delete":
        if m:
            return (doc[: m.start()] + doc[m.end():]).replace("\n\n\n", "\n\n").strip() + "\n"
        return doc

    line = f"- {edit.content.strip()}"
    if edit.op == "replace":
        block = f"{_SECTION.format(field=field)}\n{line}"
        if m:
            return doc[: m.start()] + block + doc[m.end():]
        return (doc.rstrip() + "\n\n" + block + "\n") if doc.strip() else block + "\n"

    # append (default): add the line to the field's section, creating it if absent
    if m:
        block = m.group(0).rstrip()
        if line in block:  # idempotent — don't duplicate an identical rule
            return doc
        new_block = block + "\n" + line
        return doc[: m.start()] + new_block + doc[m.end():]
    block = f"{_SECTION.format(field=field)}\n{line}"
    return (doc.rstrip() + "\n\n" + block + "\n") if doc.strip() else block + "\n"


def apply_edits(doc: str, edits: Iterable[FieldEdit]) -> str:
    """Apply a sequence of edits in order."""
    for e in edits:
        doc = apply_edit(doc, e)
    return doc


def diff_line_count(before: str, after: str) -> int:
    """Number of changed lines (added or removed) — used to assert edits stay
    bounded (no whole-prompt rewrite)."""
    b = before.splitlines()
    a = after.splitlines()
    bset, aset = set(b), set(a)
    return len(aset - bset) + len(bset - aset)
