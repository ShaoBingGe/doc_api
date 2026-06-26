"""技能/优化洞察（ADR-001 P5，只读）。

把 P1–P4 的产物在一处显性化：每字段的跨轮准确率轨迹（P1 留出分）、slow-update 守护状态
（P3，复用 `compute_guardians`）、已挂技能名（P2/P4）。纯读，不改任何状态。
"""
from __future__ import annotations

import uuid as _uuid


def build_insights(db, api_def_id: _uuid.UUID) -> dict:
    """汇总当前 API 的优化洞察（最近一次 run 的轨迹 + 守护 + active 版本各字段已挂技能）。"""
    from app.ocr_optimizer.models import (
        OcrModuleIteration,
        OcrOptimizationRound,
        OcrOptimizationRun,
        OcrSkill,
    )
    from app.ocr_optimizer.service import persistence
    from app.ocr_optimizer.skilltrain import slow_update

    # 最近一次 run → 每字段跨轮准确率轨迹
    run = (
        db.query(OcrOptimizationRun)
        .filter(OcrOptimizationRun.api_definition_id == api_def_id)
        .order_by(OcrOptimizationRun.started_at.desc())
        .first()
    )
    trajectories: dict[str, list[float]] = {}
    if run:
        rows = (
            db.query(
                OcrOptimizationRound.round_num,
                OcrModuleIteration.module_key,
                OcrModuleIteration.aggregate_accuracy,
            )
            .join(OcrModuleIteration, OcrModuleIteration.round_id == OcrOptimizationRound.id)
            .filter(
                OcrOptimizationRound.run_id == run.id,
                OcrModuleIteration.aggregate_accuracy.isnot(None),
            )
            .order_by(OcrOptimizationRound.round_num)
            .all()
        )
        for _rnum, mk, acc in rows:
            trajectories.setdefault(mk, []).append(round(float(acc), 3))

    guardians = {g.field: g for g in slow_update.compute_guardians(trajectories)}

    # active 版本各字段 → 已挂技能名
    ver = persistence.get_active_version(db, api_def_id)
    fields: list[dict] = []
    if ver is not None:
        mods = list(ver.modules)  # ordered by order_index (relationship)
        # 一次取齐所有挂载技能名
        all_uids: list[_uuid.UUID] = []
        for m in mods:
            for s in (m.skill_ids or []):
                try:
                    all_uids.append(s if isinstance(s, _uuid.UUID) else _uuid.UUID(str(s)))
                except (ValueError, TypeError):
                    continue
        name_by_id: dict[str, str] = {}
        if all_uids:
            for sk in db.query(OcrSkill).filter(OcrSkill.id.in_(all_uids)).all():
                name_by_id[str(sk.id)] = sk.name

        for m in mods:
            mk = m.module_key
            g = guardians.get(mk)
            fields.append(
                {
                    "field": mk,
                    "display_name": getattr(m, "display_name", "") or mk,
                    "trajectory": trajectories.get(mk, []),
                    "guardian": ({"kind": g.kind, "note": g.note} if g else None),
                    "skills": [
                        name_by_id[str(s)]
                        for s in (m.skill_ids or [])
                        if str(s) in name_by_id
                    ],
                }
            )

    return {
        "has_run": run is not None,
        "rounds": (run.rounds_completed if run else 0),
        "version": (ver.version if ver is not None else None),
        "fields": fields,
    }
