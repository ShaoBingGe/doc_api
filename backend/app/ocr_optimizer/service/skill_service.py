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


def _api_country(db: Session, api_def_id: uuid.UUID | None) -> str | None:
    """The API's country (config.source_country), for country-scoping globals."""
    if api_def_id is None:
        return None
    from app.models.api_definition import ApiDefinition

    api = db.get(ApiDefinition, api_def_id)
    cfg = (api.config or {}) if api is not None else {}
    c = cfg.get("source_country") if isinstance(cfg, dict) else None
    return str(c) if c else None


def list_skills(db: Session, api_def_id: uuid.UUID | None) -> list[OcrSkill]:
    """Active skills referenceable by an API = its PRIVATE skills + the GLOBAL
    skills of its COUNTRY (plus country-less universal globals). A JP global skill
    is NOT referenceable by a MY API. Pass None to list the whole global library.
    """
    q = db.query(OcrSkill).filter(OcrSkill.status == SkillStatus.active.value)
    if api_def_id is not None:
        country = _api_country(db, api_def_id)
        # global part: same-country globals + universal (country IS NULL) globals
        global_part = OcrSkill.api_definition_id.is_(None)
        if country:
            global_part = global_part & or_(
                OcrSkill.country == country, OcrSkill.country.is_(None)
            )
        q = q.filter(or_(OcrSkill.api_definition_id == api_def_id, global_part))
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
    country: str | None = None,
) -> OcrSkill:
    """Create a private (api_def_id set) or global (None) skill. Global skills
    carry a `country` scope (referenceable only by that country's APIs; NULL =
    universal). Enforces the unique (api_definition_id, name) constraint."""
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
        country=(str(country) if country else None),
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
        # CRITICAL: extraction uses the STATIC OcrPromptVersion.composed_prompt.
        # Appending to skill_ids alone does nothing until the version's prompt is
        # re-composed with the now-attached skill rendered in. Without this,
        # "attach to field" is a silent no-op. (skill_render is flag-gated by
        # SKILL_LIBRARY_RENDER; off → recompose is a no-op delta.)
        recompose_version_prompt(db, version_id)
    return mod


def recompose_version_prompt(db: Session, version_id: uuid.UUID) -> None:
    """Re-render a version's `composed_prompt`/`composed_schema` from its current
    modules (so newly attached/changed skills reach the model). Mirrors the
    run_orchestrator compose seam: customer constraints → skill render → assemble.
    Best-effort: a compose error leaves the prior prompt intact."""
    from ..models import OcrPromptVersion
    from . import composer, field_constraints, skill_render

    v = db.get(OcrPromptVersion, version_id)
    if v is None:
        return
    mods = list(v.modules)
    try:
        cg = field_constraints.enforce(db, v.api_definition_id, mods, v.country_global_text)
        sk = skill_render.resolve(db, v.api_definition_id, mods)
        v.composed_schema = composer.assemble_schema(mods)
        v.composed_prompt = composer.assemble_prompt(
            mods, country_global=cg, skill_content=sk
        )
        db.commit()
    except Exception:  # noqa: BLE001 — never break attach on a compose hiccup
        db.rollback()
