"""OcrSkill library — activate the (previously dormant) skill store (ADR-001 P2).

A skill is a reusable prompt fragment: `api_definition_id IS NULL` → GLOBAL
(shared across all APIs of the platform, admin-curated), non-null → PRIVATE to
that API. Skills are user/admin-curated CRUD — the OPTIMIZER is hard-forbidden
from writing them (design §17.7); promotion into the global library is a
separate, gated step (P4).

This service is the storage/read layer. Composer rendering (injecting a skill's
content into a module's prompt when attached) is wired separately and
flag-gated. CRUD here does not touch the prompt-assembly hot path.

⚠️ 术语消歧（"skill" 有两义，勿与反思混）:
  • 本模块的 `OcrSkill`（"技能库 / skill library"）= 面向客户/管理员、存 DB、有
    `api_definition_id`、可挂到 module 的可复用规则（本文）。
  • `reflection/skills_loader.py` 的 `Skill`（"反思路由 / reflection skill"）= 内部反思
    能力，按 edit_intent 路由反思提示词，静态、不入库、无 api_definition_id。
  代码层已可区分：本类永远带 `Ocr` 前缀；反思那个是 `reflection/` 包里的裸 `Skill`。
  详见 docs/repository-structure.md「'skill' 的两义」。
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError

from ..models import OcrSkill, SkillStatus


def list_skills(db: Session, api_def_id: uuid.UUID | None) -> list[OcrSkill]:
    """Active skills available to an API = its PRIVATE skills + all GLOBAL ones.
    Pass None to list only the global library."""
    q = db.query(OcrSkill).filter(OcrSkill.status == SkillStatus.active.value)
    if api_def_id is not None:
        q = q.filter(or_(OcrSkill.api_definition_id == api_def_id,
                         OcrSkill.api_definition_id.is_(None)))
    else:
        q = q.filter(OcrSkill.api_definition_id.is_(None))
    return q.order_by(OcrSkill.api_definition_id.is_(None).desc(), OcrSkill.name).all()


def get_skill(db: Session, skill_id: uuid.UUID) -> OcrSkill:
    sk = db.get(OcrSkill, skill_id)
    if not sk:
        raise NotFoundError(f"OcrSkill {skill_id} not found")
    return sk


def create_skill(
    db: Session,
    *,
    name: str,
    content: str,
    description: str = "",
    api_def_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> OcrSkill:
    """Create a private (api_def_id set) or global (None) skill. Enforces the
    unique (api_definition_id, name) constraint with a friendly error."""
    name = (name or "").strip()
    if not name or not (content or "").strip():
        raise ValidationError("skill 需要非空的 name 与 content")
    existing = (
        db.query(OcrSkill)
        .filter(OcrSkill.api_definition_id == api_def_id, OcrSkill.name == name)
        .first()
    )
    if existing:
        scope = "全局库" if api_def_id is None else "该 API"
        raise ValidationError(f"{scope}已存在同名 skill：{name!r}")
    sk = OcrSkill(
        id=uuid.uuid4(), api_definition_id=api_def_id, name=name,
        description=description or "", content=content,
        status=SkillStatus.active.value, created_by=created_by,
    )
    db.add(sk)
    db.commit()
    db.refresh(sk)
    return sk


def delete_skill(db: Session, skill_id: uuid.UUID) -> None:
    """Soft-deactivate a skill (status=archived) so attached modules degrade
    gracefully rather than dangling on a hard-deleted row."""
    sk = get_skill(db, skill_id)
    sk.status = SkillStatus.archived.value
    db.commit()


def attach_skill_to_module(
    db: Session, version_id: uuid.UUID, module_key: str, skill_id: uuid.UUID,
) -> "object":
    """Attach an existing skill to a module (append to module.skill_ids).
    Idempotent. Returns the updated module."""
    from sqlalchemy.orm.attributes import flag_modified

    from ..models import OcrModule

    get_skill(db, skill_id)  # 404 if missing
    mod: Optional[OcrModule] = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == version_id, OcrModule.module_key == module_key)
        .first()
    )
    if not mod:
        raise NotFoundError(f"Module {module_key!r} not found in version {version_id}")
    ids = list(mod.skill_ids or [])
    if str(skill_id) not in [str(x) for x in ids]:
        ids.append(str(skill_id))
        mod.skill_ids = ids
        flag_modified(mod, "skill_ids")
        db.commit()
        db.refresh(mod)
    return mod
