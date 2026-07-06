"""定制版本 fork / 模块克隆 / 新增字段扩展（customer_iteration 拆分第四刀）.

客户改字段 → 在源 ApiDef 上 bump 一个新 OcrPromptVersion 的全部构造逻辑：

  - `_fork_api_definition` —— Phase 19「原地 bump」：归档旧 active 版本、
    建新版本+新模块行、翻转 active 指针（api_code 不变，调用方无感）；
    应用客户 diff（rename/add/delete）+ 反思建议（累积/矛盾协调）+ FieldRule
    持久化；orphan edit → add 提升；overlay 的 renames/adds/deletes 种子化。
  - `_clone_module` —— 单模块克隆 + rename 传播 + 反思日志 + FieldRule 落库。
  - `_module_from_add_diff` / `_llm_expand_new_field` —— 新字段的 LLM 扩展。
  - `_to_snake` / `_snake` —— camelCase → snake_case（唯一实现，结构审查 F4）。

纯构造逻辑，不驱动迭代（迭代在 run_orchestrator）。函数名保持原样
（含下划线），customer_iteration 作 facade 重导出——_execute_pipeline 与
既有测试（test_overlay_first_fork / test_pending_edits / test_reconciler /
test_reflection_landing）零改动。
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.api_definition import ApiDefinition

from ..models import (
    OcrModule,
    OcrPromptVersion,
    PromptVersionStatus,
    VersionOrigin,
)
from . import composer, field_constraints

logger = logging.getLogger(__name__)

# fork 阶段 reconciler / add-field 扩展的 LLM 并发度（纯文本长 prompt 调用）。
_FORK_LLM_CONCURRENCY = 6


def _fork_api_definition(
    db: Session,
    *,
    src_api: ApiDefinition,
    src_version: OcrPromptVersion,
    src_modules: list[OcrModule],
    diffs: list[dict],
    reflections: dict[str, Any],
    user_id: uuid.UUID | None,
) -> tuple[ApiDefinition, OcrPromptVersion, list[OcrModule]]:
    """Bump source ApiDef to a new OcrPromptVersion that reflects the
    customer's edits (diffs) + reflection-agent fix suggestions.

    Function name retained for backwards compatibility / call-site
    stability; semantics changed in Phase 19. There is NO fork in the
    repo-clone sense any more — this writes new rows on the SOURCE
    ApiDef and flips its active version pointer. See `Phase 19` notes
    below + the CLAUDE.md mental-model diagram.

    Previous design created a separate ApiDef with a `-c1` api_code, which
    forced the customer to navigate to a different /workspace/api/<id>
    URL to see the iteration results. Repeated UX feedback: that breaks
    "step-by-step in one workspace" — the customer's workflow should stay
    on the source URL forever.

    New design:
      - NO new ApiDefinition row
      - NEW OcrPromptVersion on the SAME source ApiDef (next int version)
      - NEW OcrModule rows on the new version
      - source.prompt_version_id flips to point at the new version
      - source.api_code is unchanged (caller integrations don't break)
      - Iteration runs on source's docs (no document rebinding)
      - Job.new_api_definition_id == source.id (we return src_api here)

    Audit trail is preserved via the OcrPromptVersion chain
    (parent_version_id), and the prior version row stays in DB with
    status='archived'.
    """
    # Mark the prior active version as archived so the new one becomes
    # the unambiguous active version. (Mirrors _deactivate_others.)
    db.query(OcrPromptVersion).filter(
        OcrPromptVersion.api_definition_id == src_api.id,
        OcrPromptVersion.status == PromptVersionStatus.active.value,
    ).update({"status": PromptVersionStatus.archived.value})

    # Compute next integer version number on source
    used_ints: set[int] = set()
    for (label,) in (
        db.query(OcrPromptVersion.version)
        .filter(OcrPromptVersion.api_definition_id == src_api.id)
        .all()
    ):
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

    new_version = OcrPromptVersion(
        id=uuid.uuid4(),
        api_definition_id=src_api.id,
        version=str(next_version_num),
        parent_version_id=src_version.id,
        status=PromptVersionStatus.active.value,
        origin=VersionOrigin.manual_edit.value,
        composed_prompt="",
        composed_schema=None,
        # Country-wide rules are version-level and DON'T change on customize.
        # They stay the same all the way through the 3 rounds.
        country_global_text=src_version.country_global_text,
        notes=f"customer customize: v{src_version.version} → v{next_version_num} ({len(diffs)} diffs)",
        activated_at=datetime.now(timezone.utc),
    )

    # `new_api` returned to caller is the SAME src_api now pointing at the
    # new active version. Keep the variable name to minimize call-site
    # churn — every downstream reference to `new_api.id` still works
    # because new_api.id == src_api.id.
    new_api = src_api

    # Build a quick lookup of source module_keys so we can detect
    # "orphan edit diffs" — edits whose module_key has no matching source
    # module. When such an orphan carries a rename (corrected_name ≠
    # original_name), it means the customer renamed a field that the
    # source template never actually defined (e.g. MY template has
    # billFromComposite but the user renamed billFromAddress → salerAddress;
    # the LLM hallucinated billFromAddress in OCR output, and the customer
    # treated it as if it existed).
    # Promote those orphans to add diffs so a real new module is created.
    src_module_keys = {m.module_key for m in src_modules}

    # Multiple diffs may share the same module_key — e.g. several array-cell
    # corrections on `detailOfGoodsOrServices[0..N].field` all route to module
    # `detail_of_goods_or_services`. We accumulate their prompt suffixes so no
    # correction gets dropped; description and schema_type take last-wins.
    #
    # add_specs entries are (diff, reflection_key) tuples. The reflection_key
    # is computed at append time to MIRROR the reflector's keying
    # (reflector.py line 75: `diff.get("module_key") or f"_new_{idx}"`)
    # — this avoids the fragile `diffs.index(d)` lookup later, which broke
    # for Phase-7 promoted dicts that aren't members of the original list.
    edits_by_key: dict[str, dict] = {}
    add_specs: list[tuple[dict, str | None]] = []

    # ── Phase 23.2 defense-in-depth: APPLY overlay.deleted_fields to
    # src_modules right here, in addition to the same filter in
    # _execute_pipeline. This makes _fork_api_definition robust when
    # invoked directly (tests, future codepaths) and guarantees deleted
    # fields never sneak into the new version.
    try:
        from app.domain import overlay as _pes_del
        _fork_deleted = set(
            (_pes_del.get_overlay(db, src_api.id).get("deleted_fields") or [])
        )
    except Exception:  # noqa: BLE001
        _fork_deleted = set()
    if _fork_deleted:
        _fork_deleted_snake = {_snake(f) for f in _fork_deleted if f}
        _fork_deleted_all = _fork_deleted | _fork_deleted_snake
        src_modules = [
            m for m in src_modules
            if (m.module_key not in _fork_deleted_all)
            and ((m.json_path or "").split(".")[-1]
                 .replace("[*]", "").replace("[", "").replace("]", "").strip()
                 not in _fork_deleted_all)
        ]

    # ── Phase 23.2: SEED edits_by_key + add_specs from pending_edits ────
    # The overlay is the single source of truth for the customer's
    # intended field set (renames / adds / deletes). Diffs are still
    # consumed below for reflection inputs + value examples, but the
    # MODULE STRUCTURE is now driven entirely by the overlay — so a
    # rename committed via "保存到模板（立即生效）" lands in the new
    # version's modules even when the customize-submit codepath sent
    # a diff with corrected_name == original_name (or no diff at all).
    try:
        from app.domain import overlay as _pes_fork
        _fork_overlay = _pes_fork.get_overlay(db, src_api.id)
    except Exception:  # noqa: BLE001
        _fork_overlay = {}
    _overlay_renames: dict[str, str] = dict(_fork_overlay.get("renames") or {})
    _overlay_added: list[dict] = list(_fork_overlay.get("added_fields") or [])

    # (a) Seed RENAMES: for each {oldName: newName} in overlay, find the
    # source module whose json_path leaf matches oldName and stash a
    # rename patch on its module_key.
    if _overlay_renames:
        for src_m in src_modules:
            jp = src_m.json_path or ""
            leaf = jp.split(".")[-1].replace("[*]", "").replace("[", "").replace("]", "").strip()
            if not leaf:
                continue
            new_name = _overlay_renames.get(leaf)
            if not new_name or new_name == leaf:
                continue
            existing = edits_by_key.get(src_m.module_key, {})
            existing.setdefault("__rename_old", leaf)
            existing.setdefault("__rename_new", new_name)
            edits_by_key[src_m.module_key] = existing
            logger.info(
                "Phase 23.2: overlay seeded rename %r → %r on module %s",
                leaf, new_name, src_m.module_key,
            )

    # (b) Seed ADDS: for each overlay.added_fields entry not already
    # represented as a real source module, synthesize an add spec.
    src_leafs = {
        (m.json_path or "").split(".")[-1]
        .replace("[*]", "").replace("[", "").replace("]", "").strip()
        for m in src_modules
    }
    # Also exclude renames' new-names (they'll exist after rename)
    src_leafs |= set(_overlay_renames.values())
    _added_already_in_specs: set[str] = set()
    for f in _overlay_added:
        name = (f or {}).get("field_name") or ""
        if not name or name in src_leafs:
            continue
        # Build a synth diff in the same shape genuine kind=add diffs use
        synth = {
            "kind": "add",
            "module_key": _snake(name),
            "original_name": name,
            "corrected_name": name,
            "corrected_format": (f.get("type") or "string"),
        }
        # 多行明细（P0）：把 overlay 记的列定义带进 synth diff，
        # _module_from_add_diff 的 array 分支据此生成 items schema。
        if f.get("columns"):
            synth["columns"] = f["columns"]
        # Reflection for adds is keyed by module_key in the reflector
        add_specs.append((synth, synth["module_key"]))
        _added_already_in_specs.add(name)
        logger.info(
            "Phase 23.2: overlay seeded add %r (module_key=%s)",
            name, synth["module_key"],
        )

    # (c) Seed ARRAY-COLUMN structure edits（多行明细 P2）: for each array
    # field with column-level edits, stash the spec on the matching array
    # module (json_path leaf == array field name AND path ends with [*]).
    _overlay_array_columns: dict = dict(_fork_overlay.get("array_columns") or {})
    if _overlay_array_columns:
        for src_m in src_modules:
            jp = (src_m.json_path or "").strip()
            if not jp.endswith("[*]"):
                continue  # 只有数组模块有列
            leaf = jp.split(".")[-1].replace("[*]", "").replace("[", "").replace("]", "").strip()
            spec = _overlay_array_columns.get(leaf)
            if not spec:
                continue
            existing = edits_by_key.get(src_m.module_key, {})
            existing["__array_columns"] = spec
            edits_by_key[src_m.module_key] = existing
            logger.info(
                "line-items P2: overlay seeded array-column edits on module %s (%s): %s",
                src_m.module_key, leaf, spec,
            )

    # 批次5：反思结果现在按 module_key 合并（reflector 侧），同字段多条 diff
    # 的 fix_suggestions 只能追加一次——历史 bug：每条 diff 都 extend 同一份
    # 合并建议，同一建议在 __prompt_suffix 里重复 N 遍。
    _reflection_sugs_consumed: set[str] = set()

    for orig_idx, d in enumerate(diffs):
        if d.get("kind") == "edit":
            mk = d.get("module_key")
            if not mk:
                continue
            # Phase 7 fix: orphan edit diff → promote to add diff
            if mk not in src_module_keys:
                on = (d.get("original_name") or "").strip()
                cn = (d.get("corrected_name") or "").strip()
                # Synthesize an add diff using corrected_name as the new
                # field's identity. The corrected_value (if any) becomes
                # the customer's example value for LLM expansion.
                synth = dict(d)
                synth["kind"] = "add"
                synth["corrected_name"] = cn or on or mk
                if not synth.get("corrected_format"):
                    synth["corrected_format"] = "string"
                logger.info(
                    "Promoting orphan edit diff to add: module_key=%s "
                    "(original_name=%r → corrected_name=%r)",
                    mk, on, cn,
                )
                # Reflector saw this as kind=edit and keyed reflection by
                # original module_key — reuse it.
                add_specs.append((synth, mk))
                continue
            existing = edits_by_key.get(mk, {})
            r = reflections.get(mk)
            # Description: first non-empty reflection patch wins
            if r and r.description_patch and not existing.get("description"):
                existing["description"] = r.description_patch
            # Schema type: last non-empty wins
            if d.get("corrected_format") and d.get("corrected_format") != d.get("original_format"):
                existing["__schema_type"] = d["corrected_format"]
            # Rename: when corrected_name differs from original_name, propagate
            # to module_key + json_path so the fork's composed_schema emits the
            # new key. The diff's original_name may be a dotted path (e.g.
            # "detailOfGoodsOrServices[0].quantity") — only treat top-level
            # scalar renames here (no bracket / no dot).
            old_name = (d.get("original_name") or "").strip()
            new_name = (d.get("corrected_name") or "").strip()
            if (
                old_name and new_name and old_name != new_name
                and "[" not in old_name and "." not in old_name
                and "[" not in new_name and "." not in new_name
            ):
                existing["__rename_old"] = old_name
                existing["__rename_new"] = new_name
            # Suffix: accumulate per-cell hints + reflection fix_suggestions
            # （合并后的反思建议每个 module_key 只消费一次；值示例逐 diff 保留）
            suffix_parts: list[str] = []
            if r and mk not in _reflection_sugs_consumed:
                suffix_parts.extend(s for s in r.fix_suggestions if s)
                _reflection_sugs_consumed.add(mk)
            field_label = d.get("original_name") or d.get("corrected_name") or ""
            corrected_value = d.get("corrected_value")
            if corrected_value:
                if field_label and "[" in field_label:
                    suffix_parts.append(
                        f"客户在样本上修正 `{field_label}` 的值为：{corrected_value}"
                    )
                else:
                    suffix_parts.append(
                        f"客户在样本中提供的正确值示例：{corrected_value}"
                    )
            if suffix_parts:
                merged = "\n".join(suffix_parts)
                existing["__prompt_suffix"] = (
                    (existing.get("__prompt_suffix", "") + ("\n" if existing.get("__prompt_suffix") else "") + merged).strip()
                )
            if existing:
                edits_by_key[mk] = existing
        elif d.get("kind") == "add":
            # Genuine add — reflector keyed by module_key if present, else _new_{idx}
            rk = d.get("module_key") or f"_new_{orig_idx}"
            # Phase 23.2 dedup: if overlay already seeded this name as an add,
            # skip — overlay version wins (it has the customer's most recent
            # description / type).
            on = (d.get("original_name") or "").strip()
            cn = (d.get("corrected_name") or "").strip()
            if (on in _added_already_in_specs) or (cn in _added_already_in_specs):
                continue
            add_specs.append((d, rk))

    from . import reconciler as _reconciler

    from concurrent.futures import ThreadPoolExecutor
    from app.processors.factory import ProcessorFactory as _PF_fork
    _fork_proc, _fork_model = _PF_fork.resolve_spec(
        src_api.processor_type, src_api.model_name
    )

    # ── 并发预算 reconciler（fork 阶段最大头）──────────────────────────────
    # 每个累积/膨胀字段一次长 prompt LLM（~22s）。曾串行：N 字段 × 22s 是
    # fork 慢的主因。reconcile_module_prompt 纯 LLM（读已加载属性，无 DB），
    # 并发安全；_clone_module 是纯构造，留在主线程串行。
    # 统筹整合：除「有新建议 + 已有累积」外，模块体膨胀（反馈块 ≥2 或
    # 正文 >600 字符）也触发纯整合，避免无限 append；fail-open 回退盲追加。
    reconcile_targets = []
    for m in src_modules:
        r = reflections.get(m.module_key)
        if r is None:
            continue
        new_sugs = list(getattr(r, "fix_suggestions", []) or [])
        if not _reconciler.has_accumulated_feedback(m.ocr_prompt):
            continue
        # 批次6（红线⑤「矛盾才协调」）：膨胀 → 纯整合；新建议与旧反馈疑似
        # 矛盾 → LLM 裁决；否则确定性盲追加（composer 折叠去重，零信息损失、
        # 零 LLM 漂移）。历史行为是逢新建议必 LLM 重写。
        if _reconciler.is_bloated(m.ocr_prompt) or \
                _reconciler.has_contradiction(m.ocr_prompt, new_sugs):
            reconcile_targets.append((m, r, new_sugs))

    def _reconcile_one(m, r, new_sugs):
        try:
            c = _reconciler.reconcile_module_prompt(
                module_key=m.module_key, display_name=m.display_name,
                current_prompt=m.ocr_prompt or "", new_suggestions=new_sugs,
                field_rule=getattr(r, "field_rule", None),
                latest_intent=getattr(r, "diff", None) or {},
                processor_spec=_fork_proc, model_name=_fork_model,
            )
            return m.module_key, c
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile %s failed: %s", m.module_key, exc)
            return m.module_key, None

    coherent_by_key: dict[str, str] = {}
    if reconcile_targets:
        with ThreadPoolExecutor(max_workers=min(_FORK_LLM_CONCURRENCY, len(reconcile_targets))) as ex:
            for k, c in ex.map(lambda t: _reconcile_one(*t), reconcile_targets):
                if c:
                    coherent_by_key[k] = c

    # 串行 clone（纯构造，用预算好的 coherent prompt）
    new_modules: list[OcrModule] = []
    for m in src_modules:
        patch = edits_by_key.get(m.module_key, {})
        r = reflections.get(m.module_key)
        if r is not None:
            patch = dict(patch)  # don't mutate the shared dict
            patch["__reflection"] = r
            if m.module_key in coherent_by_key:
                patch["__reconciled_prompt"] = coherent_by_key[m.module_key]
        new_modules.append(_clone_module(m, new_version_id=new_version.id, patch=patch))

    order_start = max((m.order_index for m in new_modules), default=-1) + 1
    # Build a compact sibling-example block once, reuse across all add diffs.
    sibling_examples = "\n".join(
        f"- {m.module_key} ({m.display_name or ''}): {(m.description or '')[:80]}"
        for m in src_modules[:5]
    )

    # ── 并发新增字段扩展（_module_from_add_diff 纯构造 + LLM，无 DB）──────
    def _add_one(i, d, reflection_key):
        rout = reflections.get(reflection_key) if reflection_key else None
        try:
            return i, _module_from_add_diff(
                d, new_version_id=new_version.id, order_index=order_start + i,
                reflection_outputs=rout, sibling_examples=sibling_examples,
                processor_spec=_fork_proc, model_name=_fork_model,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("add field #%d failed: %s", i, exc)
            return i, None

    if len(add_specs) <= 1:
        for i, (d, rk) in enumerate(add_specs):
            _, mod = _add_one(i, d, rk)
            if mod is not None:
                new_modules.append(mod)
    else:
        with ThreadPoolExecutor(max_workers=min(_FORK_LLM_CONCURRENCY, len(add_specs))) as ex:
            add_res = list(ex.map(
                lambda x: _add_one(x[0], x[1][0], x[1][1]), list(enumerate(add_specs))
            ))
        for _i, mod in sorted(add_res, key=lambda x: x[0]):
            if mod is not None:
                new_modules.append(mod)

    try:
        # Carry customer per-field overrides into the forked (manual_edit) base
        # so every downstream round inherits them.
        _cg = field_constraints.enforce(
            db, new_version.api_definition_id, new_modules, new_version.country_global_text,
        )
        from app.ocr_optimizer.service import skill_render
        _sk = skill_render.resolve(db, new_version.api_definition_id, new_modules)
        new_version.composed_schema = composer.assemble_schema(new_modules)
        new_version.composed_prompt = composer.assemble_prompt(
            new_modules,
            country_global=_cg,
            skill_content=_sk,
        )
    except ValueError as exc:
        raise ValidationError(f"Compose failed for forked version: {exc}") from exc

    db.add(new_version)
    for m in new_modules:
        db.add(m)

    # Phase 19 — source ApiDef's active version now points to the new
    # customize version. status stays whatever it was (don't downgrade
    # an active ApiDef to draft just because we created a new prompt
    # version on it).
    new_api.prompt_version_id = new_version.id
    db.commit()

    # NOTE (design v3): inherited sample annotations are NOT auto-GT'd.
    # The customer must explicitly confirm each sample's OCR output as
    # ground truth via the confirm-gt endpoint. This avoids feeding the
    # 3-round optimizer self-referential "OCR = GT" signal.

    db.refresh(new_api)
    db.refresh(new_version)
    return new_api, new_version, new_modules


def _clone_module(src: OcrModule, *, new_version_id: uuid.UUID, patch: dict) -> OcrModule:
    new_description = patch.get("description") or src.description
    # Phase 4 — if the upstream reconciler produced a coherent (de-contradicted)
    # prompt, use it as-is (latest intent already won); otherwise fall back to
    # the §⑤.3 blind-append of this round's suggestions.
    reconciled = patch.get("__reconciled_prompt")
    if reconciled:
        new_prompt = reconciled
    else:
        new_prompt = src.ocr_prompt
        suffix = patch.get("__prompt_suffix")
        if suffix:
            from .feedback_blocks import append_feedback
            new_prompt = append_feedback(src.ocr_prompt, suffix)

    # Phase 8 — copy source ocr_suggestions and append reflection entry
    # (when there was a customer edit on this module). Unchanged modules
    # keep the source suggestions verbatim; ocr_suggestions["reflections"]
    # only gets a non-empty list for fields the customer actually touched.
    new_suggestions = copy.deepcopy(src.ocr_suggestions or {})
    if not isinstance(new_suggestions, dict):
        new_suggestions = {}
    reflection = patch.get("__reflection")
    if reflection is not None:
        diff = getattr(reflection, "diff", None) or {}
        on = (diff.get("original_name") or "").strip()
        cn = (diff.get("corrected_name") or "").strip()
        ov = diff.get("original_value")
        cv = diff.get("corrected_value")
        entry = {
            "round": 0,
            "kind": getattr(reflection, "kind", diff.get("kind", "edit")),
            "rationale": getattr(reflection, "rationale_summary", "") or "",
            "fix_suggestions": list(getattr(reflection, "fix_suggestions", []) or []),
            "original_name": on,
            "corrected_name": cn or on,
            "renamed": bool(on and cn and on != cn),
            "original_value": ov,
            "corrected_value": cv,
            # Derived: rules in the original prompt that the reflection
            # implies should be REMOVED (e.g. "保留字母前缀" when the
            # customer renamed PO and the fix says "去除前缀"). Best-effort
            # heuristic — looks for "当前提示词" + "/" + "客户" phrasing in
            # the rationale to surface a recommendation.
            "removed_rules": _extract_removed_rules(
                rationale=getattr(reflection, "rationale_summary", "") or "",
                src_prompt=src.ocr_prompt or "",
            ),
        }
        reflections_log = list(new_suggestions.get("reflections") or [])
        reflections_log.append(entry)
        new_suggestions["reflections"] = reflections_log

        # 批次5：把反思产出的结构化 FieldRule（已在 reflector 侧硬校验）
        # 持久化到 ocr_suggestions[FIELD_RULE_KEY]，与既有规则合并
        # （累积不覆盖）。composer 以附加段渲染其骨架——这是 Phase 2/3
        # 结构化设计的落地点，此前 FieldRule 从未落库，骨架渲染是死代码。
        fr_new = getattr(reflection, "field_rule", None)
        if fr_new is not None:
            from .field_rule import FIELD_RULE_KEY, FieldRule, merge_field_rules
            fr_old = FieldRule.from_dict(new_suggestions.get(FIELD_RULE_KEY))
            fr_merged = merge_field_rules(fr_old, fr_new)
            if fr_merged is not None and fr_merged.is_renderable():
                new_suggestions[FIELD_RULE_KEY] = fr_merged.to_dict()

    new_schema = src.schema_fragment
    schema_type = patch.get("__schema_type")
    if schema_type:
        new_schema = dict(src.schema_fragment or {})
        type_map = {
            "string": "STRING", "text": "STRING",
            "number": "NUMBER", "integer": "INTEGER",
            "date": "STRING", "boolean": "BOOLEAN",
            "array": "ARRAY",
        }
        mapped = type_map.get(schema_type.lower(), schema_type.upper())
        new_schema["type"] = mapped
        if schema_type.lower() == "date":
            new_schema["format"] = "date"

    # 多行明细 P2 — 数组列级结构编辑应用到 items schema + prompt。
    # 数组模块的 schema_fragment 即 items schema（json_path 尾随 [*]，
    # composer 注入 items），列就是 fragment["properties"] 的键。
    # 应用顺序 renamed → added → deleted（deleted 携带原列名，见 overlay）。
    array_cols = patch.get("__array_columns")
    array_col_hint = ""
    if array_cols and isinstance(src.schema_fragment, dict):
        new_schema = copy.deepcopy(new_schema or {})
        props = dict(new_schema.get("properties") or {})
        col_type_map = {
            "string": "STRING", "text": "STRING", "number": "NUMBER",
            "integer": "INTEGER", "date": "STRING", "boolean": "BOOLEAN",
        }
        hints: list[str] = []
        for old, new in (array_cols.get("renamed") or {}).items():
            if old in props:
                props[new] = props.pop(old)
                hints.append(f"列 `{old}` 已重命名为 `{new}`：按原语义识别，输出键用新名。")
        for c in (array_cols.get("added") or []):
            cname = (c.get("name") or "").strip()
            if cname and cname not in props:
                props[cname] = {"type": col_type_map.get(
                    (c.get("type") or "string").lower(), "STRING")}
                hints.append(f"新增列 `{cname}`（{c.get('type') or 'string'}）：每行都需输出该列，找不到时输出 null。")
        for dcol in (array_cols.get("deleted") or []):
            if dcol in props:
                props.pop(dcol)
                hints.append(f"列 `{dcol}` 已删除：不要再输出该列。")
        new_schema["properties"] = props
        if hints:
            array_col_hint = (
                "\n\n# 列结构变更（客户定义，以下列集为准）\n"
                + "\n".join(f"- {h}" for h in hints)
                + f"\n- 当前列集：{', '.join(props.keys()) or '—'}"
            )

    # Rename propagation (design v8 §3.9):
    # When the diff carried a top-level scalar rename (corrected_name ≠
    # original_name), rewrite module_key + json_path leaf so the fork's
    # assemble_schema emits the new key. The renamed_from_key is recorded
    # in the prompt suffix so the LLM still knows what semantic field to
    # extract on the page.
    new_module_key = src.module_key
    new_json_path = src.json_path
    # Phase 17 — keep optimizer's display label in sync with the
    # workspace field column. When the customer renames a field, the
    # frontend field column shows the NEW camelCase name (cascade
    # rename in Annotation.field_name). To match, the optimizer's
    # display_name becomes the new name verbatim — same format as the
    # ADD path (_module_from_add_diff sets display_name=new_name). The
    # original Chinese semantic label is preserved in `description`,
    # not lost.
    new_display_name = src.display_name
    new_description_value = new_description
    rename_old = patch.get("__rename_old")
    rename_new = patch.get("__rename_new")
    if rename_old and rename_new and rename_old != rename_new:
        new_module_key = _snake(rename_new)
        # Replace the leaf segment in json_path:
        #   $[*].billFromName  →  $[*].supplierName
        #   $.billFromName     →  $.supplierName
        if src.json_path and rename_old in src.json_path:
            new_json_path = src.json_path.replace(rename_old, rename_new)
        # Display the new field name — matches what the workspace
        # column shows + matches the ADD path's format.
        new_display_name = rename_new
        # Preserve the source's Chinese semantic anchor in description
        # so audit / future LLM passes still know what this field is.
        # Format: "<orig display> (重命名自 <old>)" prepended when not
        # already present in description.
        prefix = f"{src.display_name}（重命名自 {rename_old}）"
        if src.display_name and prefix not in (new_description or ""):
            new_description_value = (
                prefix + "。"
                + (new_description.lstrip() if new_description else "")
            ).rstrip()
        # Append rename hint to the prompt so the LLM gets explicit mapping
        rename_hint = (
            f"\n\n# 字段重命名（Part 3 §3.9）\n"
            f"该字段原命名为 `{rename_old}`，现已重命名为 `{rename_new}`。\n"
            f"请在票面上按 `{rename_old}` 的语义/位置/格式识别，但输出 JSON 时\n"
            f"key 必须使用新命名 `{rename_new}`，不要输出旧名 `{rename_old}`。"
        )
        new_prompt = (new_prompt or "").rstrip() + rename_hint

    # 多行明细 P2：列结构变更说明附加到 prompt（schema 已硬约束列集，
    # 这里让模型明确改名映射与新增列的取值要求）。
    if array_col_hint:
        new_prompt = (new_prompt or "").rstrip() + array_col_hint

    return OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=new_version_id,
        module_key=new_module_key,
        display_name=new_display_name,
        description=new_description_value,
        json_path=new_json_path,
        schema_fragment=new_schema,
        ocr_suggestions=new_suggestions,  # Phase 8: includes reflections log
        ocr_prompt=new_prompt,
        skill_ids=list(src.skill_ids or []),
        order_index=src.order_index,
        status=src.status,
        module_accuracy=None,
    )


def _extract_removed_rules(*, rationale: str, src_prompt: str) -> list[str]:
    """Best-effort surfacer: when the reflection rationale points out that
    the SOURCE prompt has an explicit instruction that contradicts the
    customer's correction (typical phrasing: "当前提示词…指示…/要求…"），
    pluck the offending clauses so downstream tooling (or a future Part 3
    optimizer pass) can decide to delete them from the new prompt.

    Returns an empty list when no obvious "remove this rule" hint is found.
    """
    if not rationale or not src_prompt:
        return []
    out: list[str] = []
    # Look for quoted chunks in rationale ("…") that ALSO appear verbatim
    # in src_prompt — those are likely the offending rules.
    import re as _re
    for m in _re.finditer(r'["“]([^"“”\n]{6,160})["”]', rationale):
        chunk = m.group(1).strip()
        if chunk and (chunk in src_prompt or chunk[:30] in src_prompt):
            out.append(chunk)
    # Dedup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped[:4]  # cap to 4 to avoid bloat


def _snake(camel: str) -> str:
    """billFromName → bill_from_name（结构审查 F4：与 _to_snake 二合一——
    此前两份实现对含非字母数字字符的字段名产出不同 module_key，同一
    rename/add 流可能命中不同模块行）。"""
    return _to_snake(camel)


_NEW_FIELD_LLM_SYSTEM = (
    "你是一个 OCR prompt 设计专家。给定一个客户新增字段（仅有名称、期望"
    "类型、可能的样例值，以及同模板里已有字段作风格参考），请输出一份"
    "完整、可直接生效的字段提取指令。返回纯 JSON，键必须包含：description"
    "（2~3 句业务含义）、ocr_prompt（多段：语义/位置锚点/格式约束/歧义"
    "辨别/找不到时怎么办）、ocr_suggestions（对象，键 semantics/position/"
    "most_common_feature/extra_features）。不要 markdown 围栏。"
)


def _llm_expand_new_field(
    *,
    diff: dict,
    schema_type: str,
    sibling_examples: str,
    processor_spec: str,
    model_name: str | None,
) -> dict | None:
    """Call an LLM to flesh out a customer-added field's prompt material.

    The customer only gives us {name, value, format}; we want a much richer
    description so the very first round has a fighting chance instead of
    relying on the optimizer to backfill the field's meaning later.
    """
    from .llm_failover import llm_text_completion_failover as _llm

    user_prompt = (
        f"# 新增字段\n"
        f"- 名称: {diff.get('corrected_name') or 'new_field'}\n"
        f"- 期望类型: {schema_type}\n"
        f"- 客户样例值: {diff.get('corrected_value') or '(未提供)'}\n\n"
        f"# 模板里已有字段（仅供风格对齐）\n"
        f"{sibling_examples or '(无)'}\n\n"
        f"按 JSON 输出：description / ocr_prompt / ocr_suggestions"
    )
    try:
        result = _llm(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=_NEW_FIELD_LLM_SYSTEM,
            user_prompt=user_prompt,
            as_json=True,
        )
        if isinstance(result, dict):
            return result
    except Exception as exc:
        logger.warning("LLM expansion for new field %s failed: %s",
                       diff.get('corrected_name'), exc)
    return None


def _sanitize_add_columns(columns: Any) -> list[dict[str, str]]:
    """规整新增数组字段的列定义为 [{name, type}]（去空/去重）。domain 层已做
    一次，这里对直传 diff（非经 overlay）再兜一次，保证 array 分支健壮。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(columns, list):
        return out
    for c in columns:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "type": str(c.get("type") or "string").strip() or "string"})
    return out


def _col_schema(col_type: str, type_map: dict[str, str]) -> dict:
    """把列类型映射为 items 里单列的 schema fragment。"""
    t = (col_type or "string").lower()
    frag = {"type": type_map.get(t, "STRING")}
    if t == "date":
        frag["format"] = "date"
    return frag


def _module_from_add_diff(
    diff: dict, *, new_version_id: uuid.UUID, order_index: int, reflection_outputs,
    sibling_examples: str = "", processor_spec: str = "gemini",
    model_name: str | None = None,
) -> OcrModule:
    """Build a new OcrModule for a customer-added field.

    Per design v4 ("分拆-局部验证-重组"): kick off an LLM call to flesh out
    description + ocr_prompt + ocr_suggestions BEFORE the first iteration
    round. Falls back to a static skeleton if the LLM is unreachable.
    """
    new_name = diff.get("corrected_name") or "new_field"
    module_key = _to_snake(new_name)
    format_str = (diff.get("corrected_format") or "string").lower()
    type_map = {
        "string": "STRING", "text": "STRING",
        "number": "NUMBER", "integer": "INTEGER",
        "date": "STRING", "boolean": "BOOLEAN", "array": "ARRAY",
    }

    # ── 多行明细分支（P0）───────────────────────────────────────────────────
    # format=array 时不走标量路径，改镜像 template_loader._build_array_module：
    # json_path=$[*].{name}[*]（record 下的数组），schema_fragment 即 items
    # schema（composer 尾随 [*] 注入 items）。有列 → items 为 object+properties；
    # 无列 → items 为 STRING（裸值数组，好过现状「ARRAY 无 items 零约束」）。
    if format_str == "array":
        is_array = True
        schema_type = "ARRAY"
        json_path = f"$[*].{new_name}[*]"
        columns = _sanitize_add_columns(diff.get("columns"))
        if columns:
            schema_fragment = {
                "type": "OBJECT",
                "properties": {
                    c["name"]: _col_schema(c["type"], type_map) for c in columns
                },
            }
            column_list = ", ".join(c["name"] for c in columns)
        else:
            schema_fragment = {"type": "STRING"}
            column_list = "（未定义列，每行为单值）"
        ocr_prompt = (
            f"你负责从文档中识别「{new_name}」（数组类字段 / 多行明细）。\n\n"
            f"输出位置（json_path）：{json_path}\n"
            f"该字段类型：ARRAY[{'OBJECT' if columns else 'STRING'}]\n\n"
            f"# 识别规则\n"
            f"客户新增的明细表字段 — 待优化器结合样本学习表定位与行切割。\n\n"
            f"# 输出形式\n"
            f"JSON 数组，每行一个{'对象，含字段：' + column_list if columns else '单值'}\n\n"
            f"# 输出要求\n"
            f"找不到对应行时输出空数组 []。"
        )
    else:
        is_array = False
        json_path = f"$[*].{new_name}"
        schema_type = type_map.get(format_str, "STRING")
        schema_fragment = {"type": schema_type}
        if format_str == "date":
            schema_fragment["format"] = "date"
        corrected_value_hint = ""
        if diff.get("corrected_value"):
            corrected_value_hint = f"客户提供的样例值：{diff['corrected_value']}"
        ocr_prompt = (
            f"你负责从文档中识别「{new_name}」字段。\n\n"
            f"输出位置（json_path）：{json_path}\n"
            f"该字段类型：{schema_type}\n\n"
            f"# 识别规则\n"
            f"{corrected_value_hint}\n\n"
            f"# 输出要求\n"
            f"找不到时输出 null。"
        )
    description = f"客户新增字段：{new_name}"
    ocr_suggestions = {
        "semantics": "客户新增 — 待优化器学习",
        "position": "客户新增 — 待优化器学习",
        "most_common_feature": "—",
        "extra_features": [],
    }

    # Try LLM expansion first
    expanded = _llm_expand_new_field(
        diff=diff,
        schema_type=schema_type,
        sibling_examples=sibling_examples,
        processor_spec=processor_spec,
        model_name=model_name,
    )
    if expanded:
        if isinstance(expanded.get("description"), str) and expanded["description"].strip():
            description = expanded["description"].strip()
        if isinstance(expanded.get("ocr_prompt"), str) and expanded["ocr_prompt"].strip():
            ocr_prompt = expanded["ocr_prompt"].strip()
        if isinstance(expanded.get("ocr_suggestions"), dict):
            ocr_suggestions = {**ocr_suggestions, **expanded["ocr_suggestions"]}

    # Reflection-skill outputs (new_field skill) take priority — they had
    # the most context (sibling examples + customer intent)
    if reflection_outputs and reflection_outputs.skill_outputs:
        for so in reflection_outputs.skill_outputs:
            out = so.get("output") or {}
            if isinstance(out, dict):
                if isinstance(out.get("ocr_prompt"), str) and out["ocr_prompt"].strip():
                    ocr_prompt = out["ocr_prompt"]
                # 多行明细（P0）：array 分支的 items schema 由客户列定义拍板，
                # 不让 new_field skill 的标量 fragment 覆盖（否则丢列约束）。
                if not is_array and isinstance(out.get("schema_fragment"), dict):
                    schema_fragment = out["schema_fragment"]
                if isinstance(out.get("module_key"), str) and out["module_key"]:
                    module_key = _to_snake(out["module_key"])
                if isinstance(out.get("description"), str) and out["description"].strip():
                    description = out["description"]

    return OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=new_version_id,
        module_key=module_key,
        display_name=new_name,
        description=description,
        json_path=json_path,
        schema_fragment=schema_fragment,
        ocr_suggestions=ocr_suggestions,
        ocr_prompt=ocr_prompt,
        skill_ids=[],
        order_index=order_index,
        status="active",
        module_accuracy=None,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


_SNAKE_RE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_RE_2 = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _to_snake(name: str) -> str:
    s1 = _SNAKE_RE_1.sub(r"\1_\2", name)
    s2 = _SNAKE_RE_2.sub(r"\1_\2", s1).lower()
    s3 = _NON_ALNUM_RE.sub("_", s2).strip("_")
    return s3 or "field"


# (C6 cleanup) — _next_customer_api_code removed.
# Phase 19 stopped generating a separate "-c1" api_code per customize
# (the customer's API URL stays the same and the prompt version bumps
# in place). The helper had no other callers.
