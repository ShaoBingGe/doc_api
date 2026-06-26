"""Resolve attached OcrSkills → per-module prompt content (ADR-001 P2).

Flag-gated (SKILL_LIBRARY_RENDER). Given a version's modules (each carrying
`skill_ids`), fetch the referenced ACTIVE skills and return
{module_key: rendered content}, which composer.assemble_prompt appends under the
module body. Default OFF → returns {} (composer unchanged). Pure read.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy.orm import Session


def resolve(db: Session, api_def_id: uuid.UUID | None, modules: Iterable) -> dict[str, str]:
    """Return {module_key: concatenated active-skill content} for modules with
    attached skills. Empty dict when the flag is off or nothing is attached."""
    from app.core.config import get_settings
    if not getattr(get_settings(), "SKILL_LIBRARY_RENDER", False):
        return {}

    from ..models import OcrSkill, SkillStatus

    needed: set[uuid.UUID] = set()
    mod_list = list(modules)
    for m in mod_list:
        for sid in (getattr(m, "skill_ids", None) or []):
            try:
                needed.add(sid if isinstance(sid, uuid.UUID) else uuid.UUID(str(sid)))
            except (ValueError, TypeError):
                continue
    if not needed:
        return {}

    rows = (
        db.query(OcrSkill)
        .filter(OcrSkill.id.in_(list(needed)), OcrSkill.status == SkillStatus.active.value)
        .all()
    )
    by_id = {str(s.id): s for s in rows}

    out: dict[str, str] = {}
    for m in mod_list:
        parts: list[str] = []
        for sid in (getattr(m, "skill_ids", None) or []):
            s = by_id.get(str(sid))
            if s and (s.content or "").strip():
                parts.append(f"- 【{s.name}】{s.content.strip()}")
        if parts:
            out[m.module_key] = "\n".join(parts)
    return out
